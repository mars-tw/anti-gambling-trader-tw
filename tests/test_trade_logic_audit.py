# -*- coding: utf-8 -*-
"""交易統計核心的聚焦回歸測試。"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.backtest.validate import holdout_validate
from core.markets import contract_multiplier, infer_market
from core.metrics.breakeven import compute_break_even
from core.metrics.performance import compute_metrics
from core.models import Market, Side, Trade, TradeLog
from core.verdict.judge import judge
from core.verdict.statistics import (
    required_sample_size_from_pnls,
    test_expectancy_positive as check_expectancy_positive,
)


def _trade(pnl: float, i: int = 0) -> Trade:
    d = datetime(2024, 1, 1) + timedelta(days=i)
    return Trade(
        symbol="X", market=Market.US_STOCK, side=Side.LONG,
        entry_time=d, exit_time=d, entry_price=100, exit_price=100,
        quantity=1, fees=0, pnl=pnl,
    )


def _expect_value_error(fn, word: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert word in str(exc)
    else:
        raise AssertionError(f"預期 ValueError({word})")


def test_break_even_targets_do_not_count_flat_trades_as_losses():
    # 勝30%、敗20%、打平50%;E=0.3*100 - 0.2*200 = -10。
    # 固定勝率時的打平盈虧比應為 0.2/0.3=2/3,不是把50%打平全算輸的 7/3。
    pnls = [100.0] * 3 + [-200.0] * 2 + [0.0] * 5
    targets = compute_break_even(compute_metrics(TradeLog([_trade(p, i) for i, p in enumerate(pnls)])))

    assert math.isclose(targets.required_payoff_ratio or 0.0, 2 / 3)
    # 打平比例維持50%,只在其餘50%交易中改善勝負:所需總勝率為 1/3。
    assert math.isclose(targets.required_win_rate or 0.0, 1 / 3)
    assert math.isclose(targets.win_rate_gap or 0.0, 1 / 30)


def test_zero_expectancy_does_not_offer_negative_zero_fee_cut():
    trades = [_trade(100, 0), _trade(-100, 1), _trade(0, 2)]
    for trade in trades:
        trade.fees = 10
    targets = compute_break_even(compute_metrics(TradeLog(trades)))
    assert targets.current_expectancy == 0
    assert targets.fee_cut_to_breakeven is None


def test_flat_trades_do_not_hide_structurally_impossible_active_win_rate():
    pnls = [1.0, -100.0] + [0.0] * 98
    targets = compute_break_even(compute_metrics(TradeLog([_trade(p, i) for i, p in enumerate(pnls)])))
    assert targets.structurally_hard
    assert targets.required_win_rate is None
    assert any("有輸贏的交易" in message for message in targets.messages)


def test_required_sample_size_honors_alpha_and_validates_probabilities():
    pnls = [11.0, -10.0] * 20
    at_five_pct = required_sample_size_from_pnls(pnls, alpha=0.05)
    at_one_pct = required_sample_size_from_pnls(pnls, alpha=0.01)
    assert at_five_pct is not None and at_one_pct is not None
    assert at_one_pct > at_five_pct

    for bad in (0.0, 1.0, -0.1):
        _expect_value_error(
            lambda bad=bad: required_sample_size_from_pnls(pnls, alpha=bad), "alpha"
        )
        _expect_value_error(
            lambda bad=bad: check_expectancy_positive(
                pnls, n_bootstrap=10, alpha=bad
            ),
            "alpha",
        )
    for bad in (0.0, 1.0, -0.1):
        _expect_value_error(
            lambda bad=bad: required_sample_size_from_pnls(pnls, power=bad),
            "power",
        )


def test_thin_edge_margin_also_covers_low_win_high_payoff_strategies():
    # 勝率20%、盈虧比4.1只比打平門檻4.0高2.5%;低勝率不代表結構就不脆弱。
    pnls = [410.0] * 20 + [-100.0] * 80
    verdict = judge(TradeLog([_trade(p, i) for i, p in enumerate(pnls)]), n_bootstrap=200)
    assert verdict.metrics.expectancy > 0
    assert any(flag.code == "thin_edge_margin" for flag in verdict.red_flags)


def test_thin_edge_margin_does_not_count_flat_trades_as_losses():
    pnls = [70.0] * 3 + [-100.0] * 2 + [0.0] * 5
    verdict = judge(TradeLog([_trade(p, i) for i, p in enumerate(pnls)]), n_bootstrap=200)
    assert math.isclose(verdict.metrics.expectancy, 1.0)
    flag = next(flag for flag in verdict.red_flags if flag.code == "thin_edge_margin")
    assert "打平門檻 0.67" in flag.message


def test_small_positive_sample_with_flat_trades_does_not_leak_negative_edge_sentinel():
    pnls = [70.0, 70.0, -100.0, 0.0, 0.0]
    verdict = judge(TradeLog([_trade(p, i) for i, p in enumerate(pnls)]), n_bootstrap=100)
    assert verdict.metrics.expectancy > 0
    assert 30 <= verdict.required_trades < 9999


def test_oos_cannot_claim_persistence_without_in_sample_significance():
    # 前70筆只有 +0.5 的微弱均值且高度波動,後30筆穩定為正。
    # 後段顯著不能倒推成「前段優勢延續」,因為前段根本沒建立優勢。
    pnls = [100.0, -99.0] * 35 + [10.0] * 30
    report = holdout_validate(
        TradeLog([_trade(p, i) for i, p in enumerate(pnls)]),
        n_bootstrap=500,
    )
    assert report.in_sample.expectancy > 0
    assert not report.in_sample.significance.is_significant
    assert report.out_sample.significance.is_significant
    assert not report.edge_persisted
    assert "樣本內未確認" in report.headline
    assert "優勢延續" not in report.headline


def test_oos_refuses_missing_time_instead_of_using_row_order():
    trades = []
    for i in range(30):
        trade = _trade(10.0, 0)
        trade.symbol = f"MISSING-TIME-{i}"
        trades.append(trade)
    report = holdout_validate(TradeLog(trades), n_bootstrap=100)
    assert not report.edge_persisted
    assert not report.available
    assert "缺少可切分" in report.headline
    assert any("列順序" in line for line in report.interpretation)


def test_oos_refuses_mixed_unknown_exit_times_without_dropping_rows():
    trades = [_trade(10.0, i) for i in range(30)]
    trades[7].exit_time_known = False
    report = holdout_validate(TradeLog(trades), n_bootstrap=100)
    assert not report.edge_persisted
    assert not report.available
    assert "缺少真實出場時間" in report.headline
    # 不可悄悄刪掉未知列再做出看似有效的 70/30 結論。
    assert report.in_sample.n_trades == 30


def test_oos_checks_missing_time_before_sorting_mixed_timezone_objects():
    aware = _trade(10.0, 0)
    aware.exit_time = aware.exit_time.replace(tzinfo=timezone.utc)
    missing = _trade(10.0, 1)
    missing.exit_time_known = False

    report = holdout_validate(TradeLog([aware, missing]), n_bootstrap=10)
    assert not report.available
    assert "缺少真實出場時間" in report.headline


def test_metrics_and_trend_do_not_sort_mixed_timezone_api_objects():
    from core.trend.timeline import analyze_trend

    aware = _trade(10.0, 0)
    aware.exit_time = aware.exit_time.replace(tzinfo=timezone.utc)
    naive = _trade(-5.0, 1)
    log = TradeLog([aware, naive])

    metrics = compute_metrics(log)
    trend = analyze_trend(log)
    assert not metrics.sequence_metrics_reliable
    assert "混用有時區與無時區" in metrics.sequence_note
    assert not trend.available


def test_oos_json_exposes_both_p_values():
    trades = [_trade(10.0, i) for i in range(40)]
    data = holdout_validate(TradeLog(trades), n_bootstrap=100).as_dict()
    for segment in (data["in_sample"], data["out_sample"]):
        assert "p_value_t" in segment
        assert "p_value_bootstrap" in segment


def test_oos_refuses_to_compare_different_pnl_currencies():
    trades = [_trade(10.0, i) for i in range(20)]
    for trade in trades[:10]:
        trade.pnl_currency = "USD"
    for trade in trades[10:]:
        trade.pnl_currency = "TWD"

    report = holdout_validate(TradeLog(trades), n_bootstrap=50)
    assert not report.available
    assert not report.edge_persisted
    assert "幣別" in report.unavailable_reason


def test_oos_split_never_separates_identical_exit_times():
    # 70/30 目標落在第二個時間群內;唯一無洩漏的切點是 20/80。
    trades = []
    for i in range(20):
        trade = _trade(10.0, 0)
        trade.symbol = f"DAY0-{i}"
        trades.append(trade)
    for i in range(80):
        trade = _trade(10.0, 1)
        trade.symbol = f"DAY1-{i}"
        trades.append(trade)
    report = holdout_validate(TradeLog(trades), n_bootstrap=100)
    assert report.in_sample.n_trades == 20
    assert report.out_sample.n_trades == 80


def test_oos_rejects_invalid_split_ratio():
    log = TradeLog([_trade(10.0, i) for i in range(30)])
    for bad in (0.0, 1.0, -0.1):
        _expect_value_error(
            lambda bad=bad: holdout_validate(log, split_ratio=bad, n_bootstrap=10),
            "split_ratio",
        )


def test_weekly_taiwan_derivatives_have_correct_multiplier_and_type():
    for symbol in ("MX1G5", "MX2G5", "MX4G5", "MX5G5"):
        assert contract_multiplier(symbol) == (50.0, True)
        assert infer_market(symbol) == Market.TW_FUTURES

    for symbol in ("TX118000G5", "TX218000G5", "TX418000G5", "TX518000G5",
                   "TXU18000G5", "TXV18000G5", "TXX18000G5", "TXY18000G5",
                   "TXZ18000G5"):
        assert contract_multiplier(symbol) == (50.0, True)
        assert infer_market(symbol) == Market.TW_OPTIONS

    # 裸三字母代號仍可能是美股;沒有完整契約碼時不可劫持。
    assert infer_market("TXU") == Market.US_STOCK


def test_us_class_share_symbols_are_not_left_unknown():
    assert infer_market("BRK.B") == Market.US_STOCK
    assert infer_market("BRK-B") == Market.US_STOCK
    assert infer_market("BTC-USD") == Market.CRYPTO


def test_common_separated_and_exotic_forex_pairs_are_recognized():
    for symbol in ("EUR/USD", "EUR-USD", "EUR_USD", "USDBRL", "USDTHB"):
        assert infer_market(symbol) == Market.FOREX
    assert infer_market("BTC-USD") == Market.CRYPTO


def test_compute_metrics_rejects_inferred_mixed_currencies_without_loader():
    tw = _trade(100, 0)
    tw.symbol = "2330"
    tw.market = Market.TW_STOCK
    us = _trade(100, 1)
    us.symbol = "AAPL"
    us.market = Market.US_STOCK

    _expect_value_error(
        lambda: compute_metrics(TradeLog([tw, us])),
        "推定出多種",
    )


def test_compute_metrics_disables_return_when_direct_pnl_currency_differs_from_notional():
    trade = _trade(3200, 0)
    trade.symbol = "AAPL"
    trade.market = Market.US_STOCK
    trade.pnl_currency = "TWD"

    metrics = compute_metrics(TradeLog([trade]))
    assert not metrics.return_metrics_reliable
    report = __import__(
        "core.trend.timeline", fromlist=["analyze_trend"]
    ).analyze_trend(TradeLog([trade]))
    assert not report.return_metrics_reliable
    assert report.decay.direction == "unknown"


def test_direct_pnl_without_currency_never_produces_return_metrics():
    trade = _trade(100, 0)
    metrics = compute_metrics(TradeLog([trade]))
    assert not metrics.currency_reliable
    assert not metrics.return_metrics_reliable


def test_leveraged_contract_notional_is_not_called_account_drawdown_basis():
    first = Trade(
        symbol="TXFG5", market=Market.TW_FUTURES, side=Side.LONG,
        entry_time=datetime(2026, 1, 1), exit_time=datetime(2026, 1, 2),
        entry_price=20000, exit_price=20100, quantity=1,
        contract_multiplier=200, pnl_currency="TWD",
    )
    second = Trade(
        symbol="TXFG5", market=Market.TW_FUTURES, side=Side.LONG,
        entry_time=datetime(2026, 1, 3), exit_time=datetime(2026, 1, 4),
        entry_price=20100, exit_price=19900, quantity=1,
        contract_multiplier=200, pnl_currency="TWD",
    )
    metrics = compute_metrics(TradeLog([first, second]))

    assert metrics.return_metrics_reliable
    assert not metrics.drawdown_pct_reliable
    assert "帳戶權益" in metrics.drawdown_note


def test_short_stock_notional_is_not_called_account_drawdown_basis():
    first = Trade(
        symbol="AAPL", market=Market.US_STOCK, side=Side.SHORT,
        entry_time=datetime(2026, 1, 1), exit_time=datetime(2026, 1, 2),
        entry_price=100, exit_price=90, quantity=10, pnl=100,
        pnl_currency="USD",
    )
    second = Trade(
        symbol="AAPL", market=Market.US_STOCK, side=Side.SHORT,
        entry_time=datetime(2026, 1, 3), exit_time=datetime(2026, 1, 4),
        entry_price=90, exit_price=120, quantity=10, pnl=-300,
        pnl_currency="USD",
    )
    metrics = compute_metrics(TradeLog([first, second]))
    assert not metrics.drawdown_pct_reliable
    assert "放空" in metrics.drawdown_note


def test_direct_trade_api_refuses_derivative_default_multiplier():
    _expect_value_error(
        lambda: Trade(
            symbol="TXFG5", market=Market.TW_FUTURES, side=Side.LONG,
            entry_time=datetime(2026, 1, 1), exit_time=datetime(2026, 1, 2),
            entry_price=20000, exit_price=20100, quantity=1,
        ),
        "contract_multiplier",
    )


if __name__ == "__main__":
    import traceback

    module = sys.modules[__name__]
    tests = [
        value for name, value in sorted(vars(module).items())
        if name.startswith("test_") and callable(value)
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {test.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
