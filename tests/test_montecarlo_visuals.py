"""Exact capital-risk model tests for the chart-oriented API."""

from __future__ import annotations

import threading

import pytest

import core.montecarlo.simulate as simulate_module
from core.montecarlo import (
    SimulationCancelled,
    simulate_capital_risk,
    threshold_amount,
)


def test_all_three_threshold_meanings_map_explicitly():
    assert threshold_amount(1000.0, "remaining_fraction", 0.10) == 100.0
    assert threshold_amount(1000.0, "remaining_amount", 10.0) == 10.0
    assert threshold_amount(1000.0, "loss_fraction", 0.10) == 900.0


def test_strict_equality_and_initially_below_are_not_confused():
    equality = simulate_capital_risk(
        [-10.0] * 10,
        start_equity=100.0,
        currency="USD",
        threshold_kind="remaining_amount",
        threshold_value=90.0,
        future_trades=3,
        paths=100,
    )
    # E=90 at step 1 is equality, not a strict hit; E=80 hits at step 2.
    assert equality["hit_count"] == 100
    assert equality["first_hit"]["median"] == 2.0
    assert equality["terminal"]["median"] == 80.0

    initial = simulate_capital_risk(
        [1.0] * 10,
        start_equity=100.0,
        currency="USD",
        threshold_kind="remaining_amount",
        threshold_value=110.0,
        future_trades=4,
        paths=100,
    )
    assert initial["initially_below"] is True
    assert initial["hit_fraction"] == 1.0
    assert initial["first_hit"]["median"] == 0.0
    assert all(point["median"] == 100.0 for point in initial["fan"])


def test_positive_zero_hits_and_negative_crossing_is_held_without_clamp():
    positive = simulate_capital_risk(
        [5.0] * 10,
        start_equity=100.0,
        currency="USD",
        threshold_kind="remaining_fraction",
        threshold_value=0.10,
        future_trades=5,
        paths=100,
    )
    assert positive["hit_count"] == 0
    assert "不代表不可能" in positive["zero_hit_note"]

    negative = simulate_capital_risk(
        [-200.0] * 10,
        start_equity=100.0,
        currency="USD",
        threshold_kind="remaining_amount",
        threshold_value=10.0,
        future_trades=5,
        paths=100,
    )
    assert negative["hit_fraction"] == 1.0
    assert negative["terminal"]["median"] == -100.0
    assert [point["median"] for point in negative["fan"]] == [
        100.0, -100.0, -100.0, -100.0, -100.0, -100.0
    ]


def test_seeded_batch_is_reproducible_and_endpoint_matches_terminal_summary():
    kwargs = dict(
        start_equity=1000.0,
        currency="TWD",
        threshold_kind="loss_fraction",
        threshold_value=0.25,
        future_trades=20,
        paths=200,
        seed=12345,
    )
    first = simulate_capital_risk([-8.0, -3.0, 0.0, 5.0, 12.0] * 2, **kwargs)
    second = simulate_capital_risk([-8.0, -3.0, 0.0, 5.0, 12.0] * 2, **kwargs)
    assert first == second
    endpoint = first["fan"][-1]
    for key in ("p05", "q1", "median", "q3", "p95"):
        assert endpoint[key] == first["terminal"][key]
    assert sum(item["count"] for item in first["terminal_histogram"]) == 200


@pytest.mark.parametrize(
    "kwargs",
    [
        {"paths": True},
        {"paths": 99},
        {"paths": 5001},
        {"future_trades": True},
        {"future_trades": 1001},
        {"future_trades": 1000, "paths": 1001},
        {"start_equity": "100"},
        {"threshold_value": float("inf")},
    ],
)
def test_parameters_are_rejected_before_sampling(kwargs):
    base = dict(
        start_equity=100.0,
        currency="USD",
        threshold_kind="remaining_fraction",
        threshold_value=0.10,
        future_trades=10,
        paths=100,
    )
    base.update(kwargs)
    with pytest.raises(ValueError):
        simulate_capital_risk([1.0] * 10, **base)


def test_cooperative_cancel_is_observed_before_sampling():
    event = threading.Event()
    event.set()
    with pytest.raises(SimulationCancelled):
        simulate_capital_risk(
            [1.0] * 10,
            start_equity=100.0,
            currency="USD",
            future_trades=100,
            paths=100,
            cancel_event=event,
        )


def test_cancel_from_terminal_progress_callback_is_not_reported_successfully():
    event = threading.Event()

    def progress(completed: int, total: int) -> None:
        if completed == total:
            event.set()

    with pytest.raises(SimulationCancelled):
        simulate_capital_risk(
            [1.0] * 10,
            start_equity=100.0,
            currency="USD",
            future_trades=10,
            paths=100,
            cancel_event=event,
            progress_callback=progress,
        )


def test_deadline_after_terminal_progress_callback_is_not_reported_successfully(
    monkeypatch,
):
    class FakeClock:
        now = 100.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()
    monkeypatch.setattr(simulate_module.time, "monotonic", clock.monotonic)

    def progress(completed: int, total: int) -> None:
        if completed == total:
            clock.now = 102.0

    with pytest.raises(TimeoutError):
        simulate_capital_risk(
            [1.0] * 10,
            start_equity=100.0,
            currency="USD",
            future_trades=10,
            paths=100,
            deadline_seconds=1.0,
            progress_callback=progress,
        )
