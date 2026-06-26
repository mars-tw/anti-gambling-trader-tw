"""核心引擎的單元測試。

執行: python -m pytest tests/ -v
(無 pytest 時可直接 python tests/test_core.py)
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.analyzer import analyze_file, analyze_log  # noqa: E402
from core.metrics.performance import compute_metrics  # noqa: E402
from core.models import Market, Side, Trade, TradeLog  # noqa: E402
from core.verdict.judge import VerdictLevel, judge  # noqa: E402
from core.verdict.statistics import (  # noqa: E402
    required_sample_size,
)
from core.verdict.statistics import (  # noqa: E402
    # 別名:避免 pytest / 簡易執行器把這個「統計函式」誤認為測試案例
    test_expectancy_positive as check_expectancy_positive,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _make_trade(pnl: float, *, win_days: float = 1.0, tag: str | None = None) -> Trade:
    """造一筆指定損益的交易(直接給 pnl,跳過價格推算)。"""
    return Trade(
        symbol="TEST",
        market=Market.US_STOCK,
        side=Side.LONG,
        entry_time=datetime(2024, 1, 1),
        exit_time=datetime(2024, 1, 1 + int(win_days)),
        entry_price=100.0,
        exit_price=100.0 + pnl / 10,
        quantity=10.0,
        fees=0.0,
        pnl=pnl,
        tag=tag,
    )


# ── Trade 模型 ───────────────────────────────────────────────
def test_trade_pnl_from_prices_long():
    t = Trade("AAPL", Market.US_STOCK, Side.LONG,
              datetime(2024, 1, 1), datetime(2024, 1, 2),
              entry_price=100, exit_price=110, quantity=10, fees=5)
    # (110-100)*10 - 5 = 95
    assert t.pnl == 95
    assert t.is_win


def test_trade_pnl_from_prices_short():
    t = Trade("AAPL", Market.US_STOCK, Side.SHORT,
              datetime(2024, 1, 1), datetime(2024, 1, 2),
              entry_price=110, exit_price=100, quantity=10, fees=5)
    # 做空:(110-100)*10 - 5 = 95
    assert t.pnl == 95
    assert t.is_win


def test_trade_explicit_pnl_overrides():
    t = _make_trade(42.0)
    assert t.pnl == 42.0


# ── 指標 ─────────────────────────────────────────────────────
def test_metrics_basic_counts():
    log = TradeLog([_make_trade(100), _make_trade(-50), _make_trade(100)])
    m = compute_metrics(log)
    assert m.total_trades == 3
    assert m.wins == 2
    assert m.losses == 1
    assert abs(m.win_rate - 2 / 3) < 1e-9
    # 期望值 = mean(100, -50, 100) = 50
    assert abs(m.expectancy - 50.0) < 1e-9


def test_metrics_payoff_and_profit_factor():
    log = TradeLog([_make_trade(200), _make_trade(-100)])
    m = compute_metrics(log)
    assert abs(m.avg_win - 200) < 1e-9
    assert abs(m.avg_loss - 100) < 1e-9
    assert abs(m.payoff_ratio - 2.0) < 1e-9
    assert abs(m.profit_factor - 2.0) < 1e-9


def test_metrics_empty_log():
    m = compute_metrics(TradeLog([]))
    assert m.total_trades == 0
    assert m.expectancy == 0.0


def test_max_consecutive_losses():
    log = TradeLog([
        _make_trade(10), _make_trade(-1), _make_trade(-1),
        _make_trade(-1), _make_trade(10), _make_trade(-1),
    ])
    m = compute_metrics(log)
    assert m.max_consecutive_losses == 3


def test_drawdown_pct_is_bounded():
    """回撤百分比不該爆衝成天文數字(回歸測試)。"""
    log = TradeLog([_make_trade(10), _make_trade(-500), _make_trade(5)])
    m = compute_metrics(log)
    assert 0.0 <= m.max_drawdown_pct <= 5.0  # 應在合理範圍


# ── 統計檢定 ─────────────────────────────────────────────────
def test_significance_clear_positive():
    # 一面倒的正期望:應顯著
    pnls = [100, 120, 90, 110, 130, 95, 105, 115, 100, 125] * 3
    res = check_expectancy_positive(pnls, n_bootstrap=2000)
    assert res.is_significant
    assert res.p_value_bootstrap < 0.05


def test_significance_noisy_breakeven_not_significant():
    # 期望值接近 0 的雜訊:不該顯著
    pnls = [100, -100, 50, -50, 80, -80, 30, -30, 60, -60] * 3
    res = check_expectancy_positive(pnls, n_bootstrap=2000)
    assert not res.is_significant


def test_significance_single_trade_never_significant():
    res = check_expectancy_positive([9999], n_bootstrap=1000)
    assert not res.is_significant


def test_required_sample_size_negative_edge():
    # 負期望(低勝率 + 差盈虧比):需求樣本極大
    n = required_sample_size(win_rate=0.3, payoff_ratio=0.5)
    assert n >= 1000


# ── 裁決 ─────────────────────────────────────────────────────
def test_verdict_negative_expectancy_is_gambling():
    log = TradeLog([_make_trade(10)] * 5 + [_make_trade(-50)] * 20)
    v = judge(log, n_bootstrap=1000)
    assert v.level == VerdictLevel.GAMBLING
    assert v.should_discourage
    assert any(rf.code == "negative_expectancy" for rf in v.red_flags)


def test_verdict_insufficient_sample():
    log = TradeLog([_make_trade(100), _make_trade(120), _make_trade(90)])
    v = judge(log, n_bootstrap=1000)
    assert v.level == VerdictLevel.INSUFFICIENT
    assert v.should_discourage


def test_verdict_clear_edge_not_discouraged():
    # 大量、穩定、正期望、低變異:應認證為優勢
    pnls = [100, 120, 90, 110, 130, -40, 105, 115, 100, 125] * 5
    log = TradeLog([_make_trade(p) for p in pnls])
    v = judge(log, n_bootstrap=2000)
    assert v.level == VerdictLevel.STATISTICAL_EDGE
    assert not v.should_discourage


# ── 端到端(用範例檔)──────────────────────────────────────────
def test_e2e_gambling_example():
    result = analyze_file(EXAMPLES / "tw_stock_gambling.csv", market_hint=Market.TW_STOCK)
    assert result.verdict.should_discourage
    assert result.verdict.level == VerdictLevel.GAMBLING
    # 策略骨架的安全閘門應啟用
    assert "_DISCOURAGED = True" in result.strategy_code


def test_e2e_edge_example():
    result = analyze_file(EXAMPLES / "us_stock_edge.csv", market_hint=Market.US_STOCK)
    assert not result.verdict.should_discourage
    assert result.verdict.level == VerdictLevel.STATISTICAL_EDGE
    assert "_DISCOURAGED = False" in result.strategy_code
    # 應辨識出「季線突破」勝率高於「均線多頭」
    wr = result.profile.per_tag_winrate
    assert wr.get("季線突破", 0) > wr.get("均線多頭", 1)


def test_e2e_outofsample_present():
    result = analyze_file(EXAMPLES / "us_stock_edge.csv", market_hint=Market.US_STOCK)
    assert result.out_of_sample.edge_persisted


if __name__ == "__main__":
    # 無 pytest 時的簡易執行器
    import traceback

    # 只跑「本模組自己定義」的測試函式(排除從 core 匯入、剛好也叫 test_* 的函式)
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
