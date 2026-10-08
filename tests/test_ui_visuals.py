"""Hand-checkable tests for descriptive UI chart payloads."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from core.backtest.validate import holdout_validate
from core.metrics.performance import compute_metrics
from core.models import Market, Side, Trade, TradeLog
from core.ui.visuals import _histogram, build_visuals, quantile_r7, summarize_pnls


def _trade(pnl: float, index: int, *, currency: str | None = "USD") -> Trade:
    exit_time = datetime(2025, 1, 2) + timedelta(days=index)
    return Trade(
        symbol=f"T{index}",
        market=Market.US_STOCK,
        side=Side.LONG,
        entry_time=exit_time - timedelta(hours=1),
        exit_time=exit_time,
        entry_price=100.0,
        exit_price=100.0,
        quantity=1.0,
        pnl=pnl,
        pnl_currency=currency,
    )


def _result(log: TradeLog):
    return SimpleNamespace(
        log=log,
        metrics=compute_metrics(log),
        out_of_sample=holdout_validate(log, n_bootstrap=40),
    )


def test_r7_quartiles_are_hand_calculated():
    values = [1.0, 2.0, 3.0, 4.0]
    summary = summarize_pnls(values)
    assert quantile_r7(values, 0.5) == 2.5
    assert summary["q1"] == 1.75
    assert summary["median"] == 2.5
    assert summary["q3"] == 3.25


def test_histogram_conserves_flat_and_extreme_values():
    flat = _histogram([7.0] * 8)
    assert flat == [{"lower": 7.0, "upper": 7.0, "count": 8}]
    extreme = _histogram([-1e308, -1.0, 0.0, 1.0, 1e308])
    assert sum(item["count"] for item in extreme) == 5
    assert all(
        item["lower"] not in (float("inf"), float("-inf"))
        and item["upper"] not in (float("inf"), float("-inf"))
        for item in extreme
    )


def test_visuals_use_core_20_10_split_and_positive_drawdown_amount():
    pnls = [10.0, -4.0, -10.0, 8.0] + [1.0] * 26
    log = TradeLog([_trade(pnl, index) for index, pnl in enumerate(pnls)], "trades.csv")
    visuals = build_visuals(_result(log))
    assert visuals["holdout"]["available"] is True
    assert visuals["holdout"]["split_index"] == 20
    assert visuals["holdout"]["in_sample"]["count"] == 20
    assert visuals["holdout"]["out_sample"]["count"] == 10
    assert visuals["historical"]["holdout_cut_index"] == 20
    assert visuals["historical"]["max_drawdown_amount"] == pytest.approx(14.0)
    assert visuals["historical"]["points"][0]["cum_pnl"] == 0.0


def test_same_time_group_is_not_split_at_target_boundary():
    trades = [_trade(1.0, index) for index in range(30)]
    # The target is 20; make trades 19,20,21 share one exit timestamp so the
    # core chooses a real boundary rather than splitting the event group.
    shared = trades[19].exit_time
    for index in (19, 20, 21):
        trades[index].exit_time = shared
        trades[index].entry_time = shared - timedelta(hours=1)
    log = TradeLog(trades, "grouped.csv")
    result = _result(log)
    visuals = build_visuals(result)
    assert visuals["holdout"]["available"] is True
    assert visuals["holdout"]["split_index"] == result.out_of_sample.in_sample.n_trades
    cut = visuals["holdout"]["split_index"]
    ordered = list(log.sorted_by_time())
    assert ordered[cut - 1].exit_time < ordered[cut].exit_time
    # Duplicate timestamps block the sequence curve but not the separately
    # available core holdout boundary.
    assert visuals["historical"]["available"] is False


def test_holdout_segments_do_not_claim_curves_for_tied_timestamp_groups():
    trades = [_trade(float(index + 1), index) for index in range(20)]
    first_group_time = datetime(2025, 1, 2)
    second_group_time = datetime(2025, 1, 3)
    for index, trade in enumerate(trades):
        timestamp = first_group_time if index < 10 else second_group_time
        trade.exit_time = timestamp
        trade.entry_time = timestamp - timedelta(hours=1)

    visuals = build_visuals(_result(TradeLog(trades, "tied-groups.csv")))

    assert visuals["historical"]["available"] is False
    assert visuals["holdout"]["available"] is True
    for segment_name in ("in_sample", "out_sample"):
        segment = visuals["holdout"][segment_name]
        assert segment["curve_available"] is False
        assert segment["curve"] == []
        assert segment["curve_reason"]
        assert segment["mean"] is not None
        assert segment["median"] is not None


def test_missing_time_currency_and_dirty_data_are_separately_gated():
    missing_time = [_trade(1.0, index) for index in range(20)]
    missing_time[0].exit_time_known = False
    time_visuals = build_visuals(_result(TradeLog(missing_time, "missing-time.csv")))
    assert time_visuals["historical"]["available"] is False
    assert time_visuals["distribution"]["available"] is True

    inferred_currency = TradeLog(
        [_trade(1.0, index, currency=None) for index in range(20)],
        "currency.csv",
    )
    currency_visuals = build_visuals(_result(inferred_currency))
    assert currency_visuals["distribution"]["available"] is False
    assert currency_visuals["risk_simulation_available"] is False

    dirty = TradeLog(
        [_trade(1.0, index) for index in range(20)],
        "dirty.csv",
        rejected_row_count=1,
        rejected_row_reasons=("bad row",),
    )
    dirty_visuals = build_visuals(_result(dirty))
    assert dirty_visuals["distribution"]["available"] is True
    assert dirty_visuals["distribution"]["integrity_complete"] is False
    assert dirty_visuals["holdout"]["available"] is False
    assert dirty_visuals["risk_simulation_available"] is False


def test_nonfinite_bool_and_unrepresentable_integer_are_rejected():
    with pytest.raises(ValueError):
        summarize_pnls([True])
    with pytest.raises(ValueError):
        summarize_pnls([float("nan")])
    with pytest.raises(ValueError):
        summarize_pnls([10**10000])
