"""引擎深度測試:成本模型、t 分布、loader 邊界、breakeven 分支、report、JSON。

補齊修繕稽核點名的測試缺口。
執行: python tests/test_engine.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.analyzer import analyze_file  # noqa: E402
from core.ingest.costs import (  # noqa: E402
    TW_DAY_TRADE_TAX_RATE,
    estimate_round_trip_cost,
)
from core.ingest.loader import infer_market, load_trades  # noqa: E402
from core.metrics.breakeven import compute_break_even  # noqa: E402
from core.metrics.performance import compute_metrics  # noqa: E402
from core.models import Market, Side, Trade, TradeLog  # noqa: E402
from core.report import render_text_report  # noqa: E402
from core.verdict.judge import VerdictLevel, judge  # noqa: E402
from core.verdict.statistics import _student_t_sf  # noqa: E402


def _trade(pnl, tag=None, day=1):
    d = datetime(2024, 1, 1 + (day % 27))
    return Trade("2330", Market.TW_STOCK, Side.LONG, d, d, 100, 100, 1000, pnl=pnl, tag=tag)


# ── 成本模型:台股當沖證交稅減半 ────────────────────────────
def test_tw_day_trade_tax_halved():
    normal = estimate_round_trip_cost(Market.TW_STOCK, Side.LONG, 1000, 1010, 1000,
                                      is_day_trade=False)
    daytrade = estimate_round_trip_cost(Market.TW_STOCK, Side.LONG, 1000, 1010, 1000,
                                        is_day_trade=True)
    # 當沖較便宜,差額應約等於賣出證交稅的一半
    assert daytrade < normal
    half_tax = 1010 * 1000 * (0.003 - TW_DAY_TRADE_TAX_RATE)
    assert abs((normal - daytrade) - half_tax) < 1.0


def test_us_tax_only_on_sell():
    # 美股 SEC 規費只在賣出端;做多的進場(買)不該收稅
    cost = estimate_round_trip_cost(Market.US_STOCK, Side.LONG, 100, 110, 100)
    assert cost > 0   # 至少有滑價成本


def test_day_trade_only_affects_tw():
    # 美股當沖旗標不影響(沒有台股證交稅)
    a = estimate_round_trip_cost(Market.US_STOCK, Side.LONG, 100, 110, 100, is_day_trade=True)
    b = estimate_round_trip_cost(Market.US_STOCK, Side.LONG, 100, 110, 100, is_day_trade=False)
    assert abs(a - b) < 1e-9


# ── t 分布:對照查表已知值 ──────────────────────────────────
def test_student_t_matches_table():
    cases = [(2.228, 10, 0.025), (1.812, 10, 0.05), (0.0, 10, 0.5)]
    for t, df, expected in cases:
        assert abs(_student_t_sf(t, df) - expected) < 0.003


# ── loader:張單位換算 ──────────────────────────────────────
def test_loader_lot_to_shares():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "lots.csv"
        f.write_text(
            "代號,方向,進場時間,出場時間,進場價,出場價,張數,策略\n"
            "2330,買,2025-01-03,2025-02-10,1000,1080,1,測試\n",
            encoding="utf-8-sig",
        )
        log = load_trades(f, market_hint=Market.TW_STOCK)
        # 1 張應換算成 1000 股
        assert log.trades[0].quantity == 1000
        assert "張" in log.source  # source 有標註換算


# ── loader:# 註解列略過 + 壞時間記錄 ──────────────────────
def test_loader_skips_comment_and_records_bad_row():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "mixed.csv"
        f.write_text(
            "代號,方向,進場時間,出場時間,進場價,出場價,數量,策略\n"
            "# 這是說明列,應被略過\n"
            "2330,買,2025-01-03,2025-02-10,1000,1080,1000,正常\n"
            "2317,買,壞掉的日期,2025-02-10,200,210,1000,壞時間\n",
            encoding="utf-8-sig",
        )
        log = load_trades(f, market_hint=Market.TW_STOCK)
        # 只有 1 筆有效(# 列不計入錯誤,壞時間列被略過)
        assert len(log) == 1
        assert log.trades[0].tag == "正常"


# ── infer_market ───────────────────────────────────────────
def test_infer_market():
    assert infer_market("2330") == Market.TW_STOCK
    assert infer_market("00878") == Market.TW_STOCK
    assert infer_market("BTCUSDT") == Market.CRYPTO
    assert infer_market("AAPL") == Market.US_STOCK


# ── breakeven 進階分支 ─────────────────────────────────────
def test_breakeven_structurally_hard():
    # 賺極小、賠極大 → 需要過高勝率,應判結構性無解
    pnls = [5] * 5 + [-500] * 5
    t = compute_break_even(compute_metrics(TradeLog([_trade(p) for p in pnls])))
    assert t.structurally_hard


def test_breakeven_fee_cut_message():
    # 期望值只差一點點為負、平均成本夠大 → 應給成本削減建議
    trades = [
        Trade("X", Market.US_STOCK, Side.LONG, datetime(2024, 1, 1),
              datetime(2024, 1, 2), 100, 100, 10, fees=20, pnl=-5)
        for _ in range(10)
    ]
    t = compute_break_even(compute_metrics(TradeLog(trades)))
    assert t.fee_cut_to_breakeven is not None
    assert any("成本" in m for m in t.messages)


# ── report 各區塊觸發 ──────────────────────────────────────
def _report_for(pnls, tags=None):
    tags = tags or [None] * len(pnls)
    log = TradeLog([_trade(p, tag=t, day=i) for i, (p, t) in enumerate(zip(pnls, tags))])
    from core.analyzer import analyze_log
    return analyze_log(log, n_bootstrap=300).text_report


def test_report_gambling_hides_sentinel():
    # 賭博報告應有勸退橫幅,且不出現 9999 哨兵
    txt = _report_for([-50] * 35)
    assert "賭博" in txt
    assert "9999" not in txt


def test_report_follow_guru_section():
    # 含跟單 tag 的虧損 → 報告應有「跟單 / 聽明牌的成績單」
    txt = _report_for([-50] * 20, tags=["聽老師明牌"] * 20)
    assert "跟單" in txt or "聽明牌" in txt


# ── JSON 序列化 ────────────────────────────────────────────
def test_as_dict_json_serializable():
    result = analyze_file(
        Path(__file__).resolve().parent.parent / "examples" / "tw_stock_gambling.csv",
        market_hint=Market.TW_STOCK,
    )
    d = result.as_dict()
    s = json.dumps(d, ensure_ascii=False)   # 不應拋例外
    back = json.loads(s)
    assert "tag_verdicts" in back          # 逐策略裁決有進 JSON
    assert back["verdict"]["required_trades"] is None   # 負期望 → None(不洩漏 9999)


# ── VerdictLevel 集中徽章 ──────────────────────────────────
def test_verdict_level_badge_and_display():
    assert VerdictLevel.GAMBLING.display_name == "賭博"
    assert "🟥" in VerdictLevel.GAMBLING.badge
    assert VerdictLevel.STATISTICAL_EDGE.display_name == "具統計優勢"


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
