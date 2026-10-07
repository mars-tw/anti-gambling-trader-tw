"""Deep-audit regressions for broker safety gates (stdlib + pytest only)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from core.broker.base import (
    AccountInfo,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderType,
    Position,
)
from core.broker.paper import PaperBroker
from core.broker.registry import BROKER_TEMPLATES
from core.scaffold import ScaffoldOptions
from core.scaffold.generator import write_project


ROOT = Path(__file__).resolve().parents[1]


# ── helpers ───────────────────────────────────────────────

def _snap(broker: PaperBroker) -> tuple:
    return (
        broker._cash,
        len(broker.fills),
        {
            s: (p.quantity, p.avg_price, p.market_price)
            for s, p in broker._positions.items()
        },
    )


class _LiveStub(BrokerAdapter):
    """Minimal live adapter for gate / cancel tests (no network)."""

    name = "live-stub"
    is_live = True

    def __init__(self) -> None:
        super().__init__()
        self.cancelled: list[str] = []
        self.placed = 0

    def connect(self) -> None:
        return None

    def get_account(self) -> AccountInfo:
        return AccountInfo(cash=0.0, equity=0.0)

    def get_positions(self) -> list[Position]:
        return []

    def get_price(self, symbol: str) -> float:
        return 1.0

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        self.placed += 1
        return OrderResult(ok=True, order_id="X")

    def cancel_order(self, order_id: str) -> bool:
        self._guard_live()
        self.cancelled.append(order_id)
        return True


# ── 1. Order.validate ─────────────────────────────────────

@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": "", "side": OrderSide.BUY, "quantity": 1},
        {"symbol": "   ", "side": OrderSide.BUY, "quantity": 1},
        {"symbol": "AAPL", "side": OrderSide.BUY, "quantity": 0},
        {"symbol": "AAPL", "side": OrderSide.BUY, "quantity": -1},
        {"symbol": "AAPL", "side": OrderSide.BUY, "quantity": float("nan")},
        {"symbol": "AAPL", "side": OrderSide.BUY, "quantity": float("inf")},
        {"symbol": "AAPL", "side": OrderSide.BUY, "quantity": True},
        {"symbol": "AAPL", "side": OrderSide.BUY, "quantity": "1"},
        {
            "symbol": "AAPL",
            "side": OrderSide.BUY,
            "quantity": 1,
            "order_type": OrderType.LIMIT,
            "limit_price": None,
        },
        {
            "symbol": "AAPL",
            "side": OrderSide.BUY,
            "quantity": 1,
            "order_type": OrderType.LIMIT,
            "limit_price": 0,
        },
        {
            "symbol": "AAPL",
            "side": OrderSide.BUY,
            "quantity": 1,
            "order_type": OrderType.LIMIT,
            "limit_price": -5,
        },
        {
            "symbol": "AAPL",
            "side": OrderSide.BUY,
            "quantity": 1,
            "order_type": OrderType.LIMIT,
            "limit_price": float("nan"),
        },
        {
            "symbol": "AAPL",
            "side": OrderSide.BUY,
            "quantity": 1,
            "order_type": OrderType.LIMIT,
            "limit_price": float("inf"),
        },
        {
            "symbol": "AAPL",
            "side": OrderSide.BUY,
            "quantity": 1,
            "order_type": OrderType.LIMIT,
            "limit_price": True,
        },
    ],
)
def test_order_validate_rejects_invalid_inputs(kwargs):
    with pytest.raises(ValueError):
        Order(**kwargs).validate()


def test_order_validate_accepts_sane_market_and_limit():
    Order("AAPL", OrderSide.BUY, 1.5).validate()
    Order(
        "AAPL", OrderSide.SELL, 2, order_type=OrderType.LIMIT, limit_price=10.0
    ).validate()


# ── 2. Strict live gate ───────────────────────────────────

@pytest.mark.parametrize("bad", [False, None, 0, 1, "true", "false", "True", 1.0])
def test_confirm_live_trading_only_literal_true_unlocks(bad):
    broker = _LiveStub()
    with pytest.raises(PermissionError):
        broker.confirm_live_trading(i_understand_the_risk=bad)  # type: ignore[arg-type]
    assert broker._live_confirmed is not True
    with pytest.raises(PermissionError):
        broker._guard_live()
    with pytest.raises(PermissionError):
        broker.place_order(Order("AAPL", OrderSide.BUY, 1))
    assert broker.placed == 0


def test_failed_confirm_resets_previously_open_lock():
    broker = _LiveStub()
    broker.confirm_live_trading(i_understand_the_risk=True)
    assert broker._live_confirmed is True
    with pytest.raises(PermissionError):
        broker.confirm_live_trading(i_understand_the_risk="true")  # type: ignore[arg-type]
    assert broker._live_confirmed is not True
    with pytest.raises(PermissionError):
        broker.cancel_order("oid-1")
    assert broker.cancelled == []


def test_literal_true_unlocks_place_and_cancel():
    broker = _LiveStub()
    broker.confirm_live_trading(i_understand_the_risk=True)
    broker.place_order(Order("AAPL", OrderSide.BUY, 1))
    assert broker.cancel_order("oid-2") is True
    assert broker.placed == 1
    assert broker.cancelled == ["oid-2"]


def test_guard_live_rejects_truthy_non_true_lock_flag():
    broker = _LiveStub()
    broker._live_confirmed = 1  # truthy but not literal True
    with pytest.raises(PermissionError):
        broker._guard_live()
    broker._live_confirmed = "true"
    with pytest.raises(PermissionError):
        broker._guard_live()


# ── 3. PaperBroker ctor / quote validation ────────────────

@pytest.mark.parametrize(
    "kwargs",
    [
        {"cash": float("nan")},
        {"cash": float("-inf")},
        {"cash": -1},
        {"fee_rate": -0.01},
        {"fee_rate": float("inf")},
        {"slippage": -0.1},
        {"slippage": 1.0},
        {"slippage": float("nan")},
        {"allow_short": 1},
        {"allow_short": "true"},
        {"allow_short": None},
    ],
)
def test_paper_broker_rejects_invalid_ctor_args(kwargs):
    with pytest.raises(ValueError):
        PaperBroker(**kwargs)


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), True, "100"])
def test_set_price_rejects_non_finite_positive(price):
    broker = PaperBroker(cash=10_000)
    with pytest.raises(ValueError):
        broker.set_price("AAPL", price)  # type: ignore[arg-type]


def test_invalid_order_does_not_mutate_state():
    broker = PaperBroker(cash=10_000, fee_rate=0.001, slippage=0.0)
    broker.set_price("AAPL", 100.0)
    before = _snap(broker)
    with pytest.raises(ValueError):
        broker.place_order(Order("AAPL", OrderSide.BUY, float("nan")))
    assert _snap(broker) == before
    with pytest.raises(ValueError):
        broker.place_order(Order("", OrderSide.BUY, 1))
    assert _snap(broker) == before
    bad = broker.place_order(
        Order("AAPL", OrderSide.BUY, 1_000_000)  # insufficient cash
    )
    assert bad.ok is False
    assert _snap(broker) == before


def test_tiny_oversell_is_rejected_without_epsilon_shortcut():
    broker = PaperBroker(cash=100.0, fee_rate=0.0, slippage=0.0)
    broker.set_price("TINY", 1.0)

    result = broker.place_order(Order("TINY", OrderSide.SELL, 5e-10))

    assert result.ok is False
    assert "超過" in result.message
    assert broker._cash == 100.0
    assert broker.get_positions() == []
    assert broker.fills == []


def test_notional_underflow_is_rejected_before_state_mutation():
    broker = PaperBroker(cash=100.0, fee_rate=0.0, slippage=0.0)
    broker.set_price("UNDERFLOW", 5e-324)

    result = broker.place_order(Order("UNDERFLOW", OrderSide.BUY, 0.1))

    assert result.ok is False
    assert "成交金額" in result.message
    assert broker._cash == 100.0
    assert broker.get_positions() == []
    assert broker.fills == []


# ── 4. Limit immediate-fill (no pending queue) ─────────────

def test_nonmarketable_limits_rejected_without_state_change():
    broker = PaperBroker(cash=10_000, fee_rate=0.0, slippage=0.0)
    broker.set_price("XYZ", 100.0)
    before = _snap(broker)

    buy = broker.place_order(
        Order(
            "XYZ",
            OrderSide.BUY,
            1,
            order_type=OrderType.LIMIT,
            limit_price=50.0,
        )
    )
    assert buy.ok is False
    assert "immediate-fill simulation has no pending orders" in buy.message
    assert _snap(broker) == before

    # Seed a long so sell path is about marketability, not short-reject
    ok_buy = broker.place_order(Order("XYZ", OrderSide.BUY, 1))
    assert ok_buy.ok is True
    after_buy = _snap(broker)

    sell = broker.place_order(
        Order(
            "XYZ",
            OrderSide.SELL,
            1,
            order_type=OrderType.LIMIT,
            limit_price=200.0,
        )
    )
    assert sell.ok is False
    assert "immediate-fill simulation has no pending orders" in sell.message
    assert _snap(broker) == after_buy


def test_fake_profit_via_stale_limit_prices_blocked():
    """Quote 100 → buy limit 50 then sell limit 200 must NOT invent +150."""
    broker = PaperBroker(cash=10_000, fee_rate=0.0, slippage=0.0)
    broker.set_price("ABC", 100.0)
    start_cash = broker._cash

    r1 = broker.place_order(
        Order("ABC", OrderSide.BUY, 1, order_type=OrderType.LIMIT, limit_price=50)
    )
    r2 = broker.place_order(
        Order("ABC", OrderSide.SELL, 1, order_type=OrderType.LIMIT, limit_price=200)
    )
    assert r1.ok is False and r2.ok is False
    assert broker._cash == start_cash
    assert broker.get_positions() == []
    assert broker.fills == []


def test_marketable_limits_fill_at_quote_within_ceiling_floor():
    broker = PaperBroker(cash=10_000, fee_rate=0.0, slippage=0.0)
    broker.set_price("QQQ", 100.0)

    buy = broker.place_order(
        Order(
            "QQQ",
            OrderSide.BUY,
            2,
            order_type=OrderType.LIMIT,
            limit_price=105.0,  # ceiling above quote → fill at 100
        )
    )
    assert buy.ok is True
    assert buy.avg_price == 100.0
    assert broker._cash == pytest.approx(10_000 - 200.0)

    sell = broker.place_order(
        Order(
            "QQQ",
            OrderSide.SELL,
            2,
            order_type=OrderType.LIMIT,
            limit_price=95.0,  # floor below quote → fill at 100
        )
    )
    assert sell.ok is True
    assert sell.avg_price == 100.0
    assert broker._cash == pytest.approx(10_000)
    assert broker.get_positions() == []


def test_short_cover_accounting_preserved():
    broker = PaperBroker(cash=10_000, fee_rate=0.0, slippage=0.0, allow_short=True)
    broker.set_price("SHORT", 50.0)
    open_short = broker.place_order(Order("SHORT", OrderSide.SELL, 2))
    assert open_short.ok is True
    assert broker._cash == pytest.approx(10_100.0)  # +100 proceeds
    pos = broker.get_positions()
    assert len(pos) == 1 and pos[0].quantity == -2

    broker.set_price("SHORT", 40.0)
    cover = broker.place_order(Order("SHORT", OrderSide.BUY, 2))
    assert cover.ok is True
    assert broker.get_positions() == []
    # Sold at 50, covered at 40 → +20 profit on 2 shares
    assert broker._cash == pytest.approx(10_020.0)


def test_weighted_average_overflow_rejected_before_state_mutation():
    broker = PaperBroker(cash=1e308, fee_rate=0.0, slippage=0.0)
    broker.set_price("HUGE", 1e308)
    # Seed a finite but extreme pre-existing position. This is a focused check
    # that preview arithmetic rejects overflow before committing a new fill.
    broker._positions["HUGE"] = Position("HUGE", 1.7, 1e308, 1e308)
    before_cash = broker._cash
    before_fills = list(broker.fills)
    before_position = (
        broker._positions["HUGE"].quantity,
        broker._positions["HUGE"].avg_price,
        broker._positions["HUGE"].market_price,
    )

    # Both products are finite, but adding them overflows while calculating
    # the weighted average. No cash/quantity/fill commit may happen first.
    second = broker.place_order(Order("HUGE", OrderSide.BUY, 0.1))
    assert second.ok is False
    assert "加權成本" in second.message
    assert broker._cash == before_cash
    assert broker.fills == before_fills
    assert (
        broker._positions["HUGE"].quantity,
        broker._positions["HUGE"].avg_price,
        broker._positions["HUGE"].market_price,
    ) == before_position


def test_price_feed_can_refresh_quote_on_rejected_limit_without_transaction_mutation():
    quotes = iter([100.0, 110.0])
    broker = PaperBroker(
        cash=10_000,
        fee_rate=0.0,
        slippage=0.0,
        price_feed=lambda _symbol: next(quotes),
    )
    assert broker.place_order(Order("FEED", OrderSide.BUY, 1)).ok is True
    cash_before = broker._cash
    fills_before = list(broker.fills)
    qty_before = broker._positions["FEED"].quantity
    avg_before = broker._positions["FEED"].avg_price

    rejected = broker.place_order(
        Order(
            "FEED",
            OrderSide.BUY,
            1,
            order_type=OrderType.LIMIT,
            limit_price=105.0,
        )
    )
    assert rejected.ok is False
    assert broker._cash == cash_before
    assert broker.fills == fills_before
    assert broker._positions["FEED"].quantity == qty_before
    assert broker._positions["FEED"].avg_price == avg_before
    assert broker._positions["FEED"].market_price == 110.0


# ── 5. Cancel guards in generated Alpaca / Tradier code ───

def _cancel_method_source(template_key: str) -> str:
    code = BROKER_TEMPLATES[template_key].code
    marker = "def cancel_order"
    assert marker in code, f"{template_key} missing cancel_order"
    chunk = code.split(marker, 1)[1]
    # stop at next top-level def inside the class body if present
    nxt = chunk.find("\n    def ")
    return chunk if nxt < 0 else chunk[:nxt]


@pytest.mark.parametrize("key", ["alpaca", "tradier"])
def test_registry_cancel_order_calls_guard_live(key):
    body = _cancel_method_source(key)
    assert "self._guard_live()" in body
    # guard must appear before the remote side effect
    guard_at = body.index("self._guard_live()")
    if key == "alpaca":
        effect_at = body.index("cancel_order_by_id")
    else:
        effect_at = body.index("requests.delete")
    assert guard_at < effect_at


def test_alpaca_cancel_via_fake_client_requires_confirm(monkeypatch):
    """Exec generated AlpacaBroker against a fake client — no network."""
    import core.broker.base as base_mod

    fake_broker_lib = types.ModuleType("broker_lib")
    for name in (
        "AccountInfo",
        "BrokerAdapter",
        "Order",
        "OrderResult",
        "OrderSide",
        "OrderType",
        "Position",
    ):
        setattr(fake_broker_lib, name, getattr(base_mod, name))
    monkeypatch.setitem(__import__("sys").modules, "broker_lib", fake_broker_lib)

    ns: dict = {}
    exec(BROKER_TEMPLATES["alpaca"].code, ns)  # noqa: S102 — template audit
    AlpacaBroker = ns["AlpacaBroker"]

    class _FakeClient:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        def cancel_order_by_id(self, order_id: str) -> None:
            self.cancelled.append(order_id)

    broker = AlpacaBroker("k", "s", paper=True)
    client = _FakeClient()
    broker.client = client

    with pytest.raises(PermissionError):
        broker.cancel_order("oid-a")
    assert client.cancelled == []

    broker.confirm_live_trading(i_understand_the_risk=True)
    assert broker.cancel_order("oid-a") is True
    assert client.cancelled == ["oid-a"]


def test_tradier_cancel_via_fake_requests_requires_confirm(monkeypatch):
    import core.broker.base as base_mod

    fake_broker_lib = types.ModuleType("broker_lib")
    for name in (
        "AccountInfo",
        "BrokerAdapter",
        "Order",
        "OrderResult",
        "OrderSide",
        "OrderType",
        "Position",
    ):
        setattr(fake_broker_lib, name, getattr(base_mod, name))
    monkeypatch.setitem(__import__("sys").modules, "broker_lib", fake_broker_lib)

    calls: list[str] = []

    class _Resp:
        ok = True

    def _fake_delete(url, headers=None):  # noqa: ANN001
        calls.append(url)
        return _Resp()

    fake_requests = types.ModuleType("requests")
    fake_requests.delete = _fake_delete  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)

    ns: dict = {}
    exec(BROKER_TEMPLATES["tradier"].code, ns)  # noqa: S102 — template audit
    TradierBroker = ns["TradierBroker"]
    broker = TradierBroker("token", "acct", sandbox=True)

    with pytest.raises(PermissionError):
        broker.cancel_order("99")
    assert calls == []

    broker.confirm_live_trading(i_understand_the_risk=True)
    assert broker.cancel_order("99") is True
    assert len(calls) == 1 and calls[0].endswith("/orders/99")


# ── 6. Generated broker_lib parity (copied base/paper) ─────

def test_standalone_broker_lib_parity_with_core_sources(tmp_path):
    """Load the actual generated standalone module and exercise its safety behavior."""
    root = write_project(
        ScaffoldOptions(project_name="parity_bot", broker="paper"), tmp_path
    )
    module_name = "generated_parity_broker_lib"
    spec = importlib.util.spec_from_file_location(module_name, root / "broker_lib.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        gen = module.PaperBroker(cash=5_000, fee_rate=0.0, slippage=0.0)
        gen.set_price("PARITY", 10.0)
        with pytest.raises(ValueError):
            module.Order("PARITY", module.OrderSide.BUY, True).validate()
        rejected = gen.place_order(
            module.Order(
                "PARITY",
                module.OrderSide.BUY,
                1,
                order_type=module.OrderType.LIMIT,
                limit_price=5.0,
            )
        )
        assert rejected.ok is False
        assert "immediate-fill simulation has no pending orders" in rejected.message
        assert gen._cash == 5_000
    finally:
        sys.modules.pop(module_name, None)


def test_pyproject_dev_extra_includes_pyyaml_only():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dev = ["pytest>=7.0", "PyYAML>=6.0"]' in text
    # core stays zero-dependency
    assert "dependencies = []" in text
