"""反詐騙模組測試。

執行: python tests/test_antiscam.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.antiscam import SCAM_PATTERNS, scam_warnings_for  # noqa: E402
from core.antiscam.checklist import CHECK_ITEMS, evaluate, run_scam_check  # noqa: E402
from core.metrics.performance import compute_metrics  # noqa: E402
from core.strategy.profiler import profile_strategy  # noqa: E402
from core.models import Market, Side, Trade, TradeLog  # noqa: E402


def _trade(pnl, tag=None, same_day=True):
    exit_t = datetime(2024, 1, 1) if same_day else datetime(2024, 2, 1)
    return Trade("2330", Market.TW_STOCK, Side.LONG, datetime(2024, 1, 1),
                 exit_t, 100, 100, 1000, pnl=pnl, tag=tag)


# ── 知識庫 ───────────────────────────────────────────────────
def test_patterns_cover_four_types():
    codes = {p.code for p in SCAM_PATTERNS}
    assert codes == {
        "fake_stock_group", "guaranteed_return", "fake_guru", "fake_platform",
    }
    for p in SCAM_PATTERNS:
        assert p.rebuttal  # 每種詐騙都要有反詐金句
        assert p.red_flags


# ── 檢測清單 ─────────────────────────────────────────────────
def test_checklist_all_clear_is_low_risk():
    r = evaluate({})  # 全部沒命中
    assert r.risk_level == "低"
    assert r.score == 0


def test_checklist_hard_hit_is_extreme():
    # 命中一個權重 3 的鐵證(要匯款到平台)即為極高
    r = evaluate({"transfer_platform": True})
    assert r.risk_level == "極高"


def test_checklist_fake_group_victim():
    r = evaluate({
        "pulled_in": True, "teacher_calls": True, "upgrade_vip": True,
    })
    assert r.risk_level == "極高"
    assert any(p.code == "fake_stock_group" for p in r.hit_patterns)


def test_run_scam_check_with_injected_io():
    # 注入 input/output,模擬互動(全部回答 y)
    outputs = []
    answers = iter(["y"] * len(CHECK_ITEMS))
    r = run_scam_check(input_fn=lambda _: next(answers), output_fn=outputs.append)
    assert r.risk_level == "極高"
    assert any("反詐" in o or "風險" in o for o in outputs)


# ── 從交易分析反推詐騙警語 ──────────────────────────────────
def test_signal_fake_guru_follow_tag_loss():
    # tag 含「老師」+ 整體虧損 → 假飆股群警語
    log = TradeLog([_trade(-50, tag="老師明牌") for _ in range(10)])
    m = compute_metrics(log)
    p = profile_strategy(log)
    warnings = scam_warnings_for(m, p)
    assert any("假飆股群" in w or "假老師" in w for w in warnings)


def test_signal_high_winrate_negative_expectancy():
    # 高勝率但負期望(賺小賠大)→ 高勝率話術警語
    pnls = [10] * 7 + [-50] * 3   # 勝率 70%,期望 = (70 - 150)/10 = -8
    log = TradeLog([_trade(p) for p in pnls])
    m = compute_metrics(log)
    pr = profile_strategy(log)
    warnings = scam_warnings_for(m, pr)
    assert any("高勝率" in w for w in warnings)


def test_signal_clean_no_warnings():
    # 穩定獲利、無可疑 tag → 不應有詐騙警語
    log = TradeLog([_trade(100, tag="季線突破", same_day=False) for _ in range(10)])
    m = compute_metrics(log)
    p = profile_strategy(log)
    warnings = scam_warnings_for(m, p)
    assert warnings == []


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
