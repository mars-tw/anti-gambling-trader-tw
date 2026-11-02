"""Offline regressions for stable ruin math and generated backtest order state."""

from __future__ import annotations

from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import Market, Side
from core.montecarlo.simulate import gambler_ruin_probability
from core.strategy.skeleton import generate_skeleton


def _direct_ruin(start: int, target: int, probability: float) -> float:
    """Small-N reference where direct powers cannot overflow."""

    if probability == 0.5:
        return 1.0 - start / target
    ratio = (1.0 - probability) / probability
    return (ratio**start - ratio**target) / (1.0 - ratio**target)


@pytest.mark.parametrize("start,target,probability", [(1, 4, 0.3), (2, 7, 0.7), (3, 11, 0.5)])
def test_gambler_ruin_matches_small_direct_oracle(start, target, probability):
    assert gambler_ruin_probability(start, target, probability) == pytest.approx(
        _direct_ruin(start, target, probability), abs=1e-14
    )


@pytest.mark.parametrize("start,target,probability", [(1000, 2000, 0.3), (5000, 10000, 0.45)])
def test_gambler_ruin_large_valid_inputs_are_finite(start, target, probability):
    value = gambler_ruin_probability(start, target, probability)
    assert 0.0 <= value <= 1.0


def test_gambler_ruin_near_fair_uses_the_actual_probability_not_a_cutoff():
    probability = 0.5000000000001
    start, target = 40_000, 100_000
    with localcontext() as context:
        context.prec = 70
        p = Decimal.from_float(probability)
        ratio = (Decimal(1) - p) / p
        expected = (ratio**start - ratio**target) / (Decimal(1) - ratio**target)
    assert gambler_ruin_probability(start, target, probability) == pytest.approx(float(expected), abs=1e-12)


@pytest.mark.parametrize("probability", [float("nan"), float("inf"), -0.01, 1.01])
def test_gambler_ruin_rejects_invalid_probability(probability):
    with pytest.raises(ValueError, match="p_win"):
        gambler_ruin_probability(1, 3, probability)


def _generated_backtrader_source() -> str:
    profile = SimpleNamespace(
        style="測試",
        style_code="swing_short",
        dominant_market=Market.US_STOCK,
        dominant_side=Side.LONG,
        avg_holding_days=1.0,
        side_metrics_available=True,
    )
    verdict = SimpleNamespace(
        level=SimpleNamespace(value="statistical_edge"),
        should_discourage=False,
        headline="測試用",
        reasons=[],
        red_flags=[],
    )
    return generate_skeleton(profile, verdict, framework="backtrader").replace(
        "signal = False", "signal = len(self) == 1", 1
    )


def test_generated_backtrader_template_clears_only_current_terminal_order(tmp_path: Path):
    bt = pytest.importorskip("backtrader")
    namespace: dict[str, object] = {}
    exec(_generated_backtrader_source(), namespace)
    strategy_type = namespace["ReverseEngineeredStrategy"]

    class FakeOrder:
        Submitted, Accepted, Partial, Completed, Canceled, Expired, Margin, Rejected = range(8)

        def __init__(self, ref: int, status: int):
            self.ref = ref
            self.status = status

    current = FakeOrder(2, FakeOrder.Submitted)
    strategy = SimpleNamespace(order=current)
    strategy_type.notify_order(strategy, current)
    assert strategy.order is current
    strategy_type.notify_order(strategy, FakeOrder(1, FakeOrder.Completed))
    assert strategy.order is current
    strategy_type.notify_order(strategy, FakeOrder(2, FakeOrder.Completed))
    assert strategy.order is None

    data_file = tmp_path / "five-bars.csv"
    data_file.write_text(
        "date,open,high,low,close,volume\n"
        "2026-01-01,100,100,100,100,1\n"
        "2026-01-02,100,100,100,100,1\n"
        "2026-01-03,80,80,80,80,1\n"
        "2026-01-04,80,80,80,80,1\n"
        "2026-01-05,80,80,80,80,1\n",
        encoding="utf-8",
    )
    feed = bt.feeds.GenericCSVData(
        dataname=str(data_file),
        dtformat="%Y-%m-%d",
        datetime=0,
        open=1,
        high=2,
        low=3,
        close=4,
        volume=5,
        openinterest=-1,
        headers=True,
    )
    cerebro = bt.Cerebro()
    cerebro.adddata(feed)
    cerebro.addstrategy(strategy_type)
    completed = cerebro.run()[0]
    assert completed.position.size == 0
