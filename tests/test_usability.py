"""快速設定與實用性強化的測試。

涵蓋:per-tag 裁決、轉正數字、反事實、跟單抽算、CLI demo/init-template/--field。
執行: python tests/test_usability.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cli import main  # noqa: E402
from core.metrics.breakeven import compute_break_even  # noqa: E402
from core.metrics.performance import compute_metrics  # noqa: E402
from core.models import Market, Side, Trade, TradeLog  # noqa: E402
from core.strategy.per_tag import (  # noqa: E402
    counterfactual_drop_worst,
    follow_the_guru,
    per_tag_verdicts,
)


def _trade(pnl, tag=None, day=1):
    d = datetime(2024, 1, 1) + timedelta(days=day)
    return Trade("2330", Market.TW_STOCK, Side.LONG, d, d, 100, 100, 1000, pnl=pnl, tag=tag)


# ── 轉正數字 ─────────────────────────────────────────────────
def test_breakeven_positive_already():
    log = TradeLog([_trade(100) for _ in range(10)])
    t = compute_break_even(compute_metrics(log))
    assert t.already_positive


def test_breakeven_negative_gives_targets():
    # 勝率 30%、賺小賠大 → 應給出要轉正的勝率與盈虧比
    pnls = [50] * 3 + [-50] * 7
    log = TradeLog([_trade(p) for p in pnls])
    t = compute_break_even(compute_metrics(log))
    assert not t.already_positive
    assert t.required_win_rate is not None
    assert t.required_win_rate > 0.3   # 需要比現在更高的勝率
    assert len(t.messages) >= 1


# ── per-tag 裁決 ─────────────────────────────────────────────
def test_per_tag_sorts_worst_first():
    # 「壞招」期望值低,「好招」期望值高 → 壞招應排最前
    trades = [_trade(-100, tag="壞招", day=i) for i in range(10)]
    trades += [_trade(100, tag="好招", day=i + 20) for i in range(10)]
    log = TradeLog(trades)
    tv = per_tag_verdicts(log, n_bootstrap=300)
    assert len(tv) == 2
    assert tv[0].tag == "壞招"          # 最差排最前
    assert tv[0].expectancy < tv[1].expectancy


def test_counterfactual_drop_worst():
    # 砍掉壞招後,整體期望值應改善
    trades = [_trade(-200, tag="壞招", day=i) for i in range(15)]
    trades += [_trade(100, tag="好招", day=i + 30) for i in range(15)]
    log = TradeLog(trades)
    cf = counterfactual_drop_worst(log, n_bootstrap=300)
    assert cf is not None
    assert cf.worst_tag == "壞招"
    assert cf.after_expectancy > cf.before_expectancy


# ── 跟單抽算(反詐實用化)──────────────────────────────────
def test_follow_guru_extracts_and_judges():
    # 跟單交易虧損 → 應抽出並給出負期望的鐵證
    trades = [_trade(-100, tag="聽老師明牌", day=i) for i in range(10)]
    trades += [_trade(50, tag="自己研究", day=i + 20) for i in range(10)]
    log = TradeLog(trades)
    fg = follow_the_guru(log, n_bootstrap=300)
    assert fg is not None
    assert fg.n_trades == 10
    assert fg.expectancy < 0
    assert "鐵證" in fg.message or "賠" in fg.message


def test_follow_guru_none_when_no_follow_tags():
    log = TradeLog([_trade(100, tag="季線突破") for _ in range(10)])
    assert follow_the_guru(log) is None


# ── CLI 快速設定 ─────────────────────────────────────────────
def test_cli_demo_runs():
    # demo 是展示指令,固定回傳 0(無論範例裁決如何),只要能跑完不報錯
    rc = main(["demo"])
    assert rc == 0


def test_cli_demo_edge_runs():
    rc = main(["demo", "--edge"])
    assert rc == 0


def test_cli_analyze_no_file_guides():
    rc = main(["analyze"])
    assert rc == 1   # 無檔案 → 導流提示 → exit 1


def test_cli_analyze_example():
    rc = main(["analyze", "--example", "us"])
    assert rc == 0   # 美股優勢範例


def test_cli_init_template_then_analyze():
    with tempfile.TemporaryDirectory() as d:
        tpl = Path(d) / "tpl.csv"
        rc = main(["init-template", "--out", str(tpl)])
        assert rc == 0
        assert tpl.exists()
        # 用範本(含 # 註解列)分析,應能載入範例交易
        rc2 = main(["analyze", str(tpl)])
        assert rc2 in (0, 2)   # 範例資料可能任一裁決,只要不報錯


def test_cli_field_override():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "weird.csv"
        f.write_text(
            "股票,買賣,買進日,賣出日,買價,賣價,股,招式\n"
            "2330,買,2025-01-03,2025-02-10,1000,1080,1000,季線\n",
            encoding="utf-8-sig",
        )
        rc = main([
            "analyze", str(f),
            "--field", "symbol=股票", "--field", "entry_price=買價",
            "--field", "exit_price=賣價", "--field", "quantity=股",
            "--market", "tw_stock",
        ])
        assert rc in (0, 2)   # 能成功載入並分析(不報錯)


if __name__ == "__main__":
    import traceback

    _this = sys.modules[__name__]
    fns = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
        and getattr(v, "__module__", None) == _this.__name__
    ]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
