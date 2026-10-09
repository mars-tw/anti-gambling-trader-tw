"""Convert complete API datasets into conservative closed-trade logs."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.models import Market, Side, Trade, TradeLog
from core.data_api.models import (
    MAX_DATASET_BYTES,
    MAX_RETURNED_ROWS,
    PROVIDERS,
    normalize_decimal,
)

_ASSET = re.compile(r"^[A-Za-z0-9]{1,20}$", re.ASCII)
_CURRENCY = re.compile(r"^[A-Z]{3}$", re.ASCII)
_TAIPEI_FIXED_OFFSET_MIN_DATE = date(1980, 1, 1)

try:
    _TAIPEI = ZoneInfo("Asia/Taipei")
    _TAIPEI_IS_FIXED_FALLBACK = False
except ZoneInfoNotFoundError:
    # Windows Python installations need not ship an IANA database.  Taiwan has
    # had UTC+08 without DST since 1980, so this keeps modern API data usable
    # without adding a runtime tzdata dependency.  Older dates fail closed.
    _TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")
    _TAIPEI_IS_FIXED_FALLBACK = True


class _TaipeiFallbackHistoryUnavailable(ValueError):
    """Raised when fixed UTC+08 cannot safely represent a historical date."""


def _result(
    available: bool,
    reason: str,
    *,
    log: TradeLog | None = None,
    cycles: list[dict[str, Any]] | None = None,
    unresolved_count: int = 0,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "available": available,
        "reason": reason,
        "log": log,
        "cycles": list(cycles or []),
        "unresolved_count": unresolved_count,
        "warnings": list(warnings or []),
    }


def _int(value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("integer")
    return value


def _decimal(value: Any, *, signed: bool = False) -> Decimal:
    return Decimal(normalize_decimal(value, allow_negative=signed))


def _decimal_text(value: Decimal) -> str:
    return normalize_decimal(value, allow_negative=True)


def _finite_float(value: Decimal) -> float:
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("float_range")
    return converted


def _safe_asset(value: Any) -> str:
    if not isinstance(value, str) or _ASSET.fullmatch(value) is None:
        raise ValueError("asset")
    return value


def _safe_identifier(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("identifier")
    if isinstance(value, int):
        value = str(value)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("identifier")
    return value


def _dataset_is_bounded(dataset: Any) -> bool:
    if not isinstance(dataset, dict):
        return False
    try:
        payload = json.dumps(
            dataset,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        return False
    return len(payload) <= MAX_DATASET_BYTES


def _coverage_complete(
    dataset: dict[str, Any],
    *,
    allow_exact_fill_duplicates: bool = False,
) -> bool:
    coverage = dataset.get("coverage")
    if not isinstance(coverage, dict):
        return False
    if coverage.get("complete") is not True:
        return False
    if coverage.get("pages_complete") is not True:
        return False
    if coverage.get("boundary_complete") is not True:
        return False
    if coverage.get("truncated") is not False:
        return False
    try:
        rejected = _int(coverage.get("rejected_rows"))
        duplicates = _int(coverage.get("duplicate_rows"))
        raw_count = _int(coverage.get("raw_count"))
    except ValueError:
        return False
    reasons = coverage.get("reasons")
    rows = dataset.get("rows")
    if rejected != 0:
        return False
    if not isinstance(reasons, list):
        return False
    if reasons:
        if not (
            allow_exact_fill_duplicates
            and duplicates > 0
            and all(reason == "duplicate_rows" for reason in reasons)
        ):
            return False
    if duplicates != 0 and not allow_exact_fill_duplicates:
        return False
    if not isinstance(rows, list) or raw_count < len(rows):
        return False
    return True


def _private_provenance_verified(dataset: dict[str, Any]) -> bool:
    provenance = dataset.get("provenance")
    return (
        isinstance(provenance, dict)
        and provenance.get("authentication_verified") is True
        and provenance.get("source_scope") == "selected-symbol"
        and isinstance(provenance.get("data_type"), str)
        and bool(provenance.get("data_type"))
    )


def _valid_envelope(dataset: dict[str, Any]) -> bool:
    provider = dataset.get("provider")
    rows = dataset.get("rows")
    return (
        dataset.get("schema_version") == "api-data-v1"
        and isinstance(provider, str)
        and provider in PROVIDERS
        and isinstance(dataset.get("kind"), str)
        and isinstance(dataset.get("market"), str)
        and isinstance(dataset.get("symbol"), str)
        and bool(dataset.get("symbol"))
        and isinstance(rows, list)
        and len(rows) <= MAX_RETURNED_ROWS
    )


@dataclass(frozen=True)
class _Fill:
    fill_id: str
    order_id: str
    side: str
    time_ms: int
    symbol: str
    base_asset: str
    quote_asset: str
    quantity: Decimal
    quote_amount: Decimal
    price: Decimal
    fee_amount: Decimal
    fee_asset: str | None
    quote_mismatch: bool


@dataclass
class _Lot:
    quantity: Decimal
    cost: Decimal


@dataclass
class _Cycle:
    symbol: str
    base_asset: str
    quote_asset: str
    start_ms: int
    end_ms: int | None = None
    fill_count: int = 0
    bought_quantity: Decimal = Decimal(0)
    sold_quantity: Decimal = Decimal(0)
    acquired_quantity: Decimal = Decimal(0)
    consumed_quantity: Decimal = Decimal(0)
    total_cost: Decimal = Decimal(0)
    total_proceeds: Decimal = Decimal(0)
    allocated_cost: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    fee_quote_equivalent: Decimal = Decimal(0)
    base_fee_seen: bool = False


def _parse_fill(row: Any, dataset_symbol: str) -> _Fill:
    if not isinstance(row, dict):
        raise ValueError("fill")
    fill_id = _safe_identifier(row.get("id"))
    order_id = _safe_identifier(row.get("order_id"))
    side = row.get("side")
    if not isinstance(side, str) or side not in {"BUY", "SELL"}:
        raise ValueError("fill")
    symbol = row.get("symbol")
    if not isinstance(symbol, str) or symbol != dataset_symbol:
        raise ValueError("fill")
    time_ms = _int(row.get("time_ms"))
    base_asset = _safe_asset(row.get("base_asset"))
    quote_asset = _safe_asset(row.get("quote_asset"))
    if base_asset == quote_asset:
        raise ValueError("fill")
    quantity = _decimal(row.get("quantity"))
    quote_amount = _decimal(row.get("quote_amount"))
    price = _decimal(row.get("price"))
    # A missing fee is unknown source data, not an observed zero.  Keeping the
    # distinction is required for a closed-trade basis to be auditable.
    if "fee_amount" not in row or row.get("fee_amount") is None:
        raise ValueError("fill")
    fee_amount = _decimal(row["fee_amount"])
    if quantity <= 0 or quote_amount <= 0 or price <= 0:
        raise ValueError("fill")
    raw_fee_asset = row.get("fee_asset")
    if fee_amount == 0:
        fee_asset = None
        if raw_fee_asset not in (None, ""):
            fee_asset = _safe_asset(raw_fee_asset)
    else:
        fee_asset = _safe_asset(raw_fee_asset)
        if fee_asset not in {base_asset, quote_asset}:
            raise ValueError("unsupported_fee_asset")
    return _Fill(
        fill_id=fill_id,
        order_id=order_id,
        side=side,
        time_ms=time_ms,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        quantity=quantity,
        quote_amount=quote_amount,
        price=price,
        fee_amount=fee_amount,
        fee_asset=fee_asset,
        quote_mismatch=quote_amount != price * quantity,
    )


def _fill_scope(dataset: dict[str, Any]) -> tuple[str, str, str]:
    requested = dataset.get("requested")
    if not isinstance(requested, dict):
        raise ValueError("fill_scope")
    account_ref = requested.get("account_ref")
    if account_ref is None:
        # A private dataset without an opaque account reference is still bound
        # to its one authenticated credential selection; never combine it with
        # a different dataset's selection when evaluating a fill ID.
        account_scope = "__authenticated_default__"
    else:
        account_scope = _safe_identifier(account_ref)
    return (dataset["provider"], account_scope, dataset["symbol"])


def _deduplicate_fills(
    fills: list[_Fill],
    *,
    scope: tuple[str, str, str],
) -> list[_Fill]:
    seen: dict[tuple[str, str, str, str], _Fill] = {}
    unique: list[_Fill] = []
    for fill in fills:
        key = (*scope, fill.fill_id)
        previous = seen.get(key)
        if previous is None:
            seen[key] = fill
            unique.append(fill)
        elif previous != fill:
            raise ValueError("conflicting_fill_id")
    return unique


def _consume_fifo(lots: list[_Lot], quantity: Decimal) -> Decimal:
    remaining = quantity
    allocated = Decimal(0)
    while remaining > 0:
        if not lots:
            raise ValueError("insufficient_inventory")
        lot = lots[0]
        take = min(lot.quantity, remaining)
        if take == lot.quantity:
            cost = lot.cost
            lots.pop(0)
        else:
            cost = lot.cost * take / lot.quantity
            lot.quantity -= take
            lot.cost -= cost
        allocated += cost
        remaining -= take
    return allocated


def _cycle_summary(
    cycle: _Cycle,
    *,
    status: str,
    remaining_quantity: Decimal,
    remaining_cost: Decimal,
) -> dict[str, Any]:
    return {
        "status": status,
        "symbol": cycle.symbol,
        "market": "spot",
        "base_asset": cycle.base_asset,
        "quote_asset": cycle.quote_asset,
        "entry_time_ms": cycle.start_ms,
        "exit_time_ms": cycle.end_ms,
        "fill_count": cycle.fill_count,
        "bought_quantity": _decimal_text(cycle.bought_quantity),
        "sold_quantity": _decimal_text(cycle.sold_quantity),
        "acquired_quantity": _decimal_text(cycle.acquired_quantity),
        "consumed_quantity": _decimal_text(cycle.consumed_quantity),
        "remaining_quantity": _decimal_text(remaining_quantity),
        "remaining_cost": _decimal_text(remaining_cost),
        "realized_pnl": _decimal_text(cycle.realized_pnl),
        "net_pnl": (
            _decimal_text(cycle.total_proceeds - cycle.total_cost)
            if status == "closed"
            else None
        ),
        "fee_quote_equivalent": _decimal_text(cycle.fee_quote_equivalent),
    }


def _cycle_trade(cycle: _Cycle, provider: str) -> Trade:
    if (
        cycle.end_ms is None
        or cycle.acquired_quantity <= 0
        or cycle.consumed_quantity <= 0
        or cycle.acquired_quantity != cycle.consumed_quantity
    ):
        raise ValueError("cycle")
    entry_price = cycle.total_cost / cycle.acquired_quantity
    exit_price = cycle.total_proceeds / cycle.consumed_quantity
    net_pnl = cycle.total_proceeds - cycle.total_cost
    return Trade(
        symbol=cycle.symbol,
        market=Market.CRYPTO,
        side=Side.LONG,
        entry_time=datetime.fromtimestamp(cycle.start_ms / 1000, tz=timezone.utc),
        exit_time=datetime.fromtimestamp(cycle.end_ms / 1000, tz=timezone.utc),
        entry_price=_finite_float(entry_price),
        exit_price=_finite_float(exit_price),
        quantity=_finite_float(cycle.consumed_quantity),
        fees=_finite_float(cycle.fee_quote_equivalent),
        pnl=_finite_float(net_pnl),
        tag=f"api:{provider}:fifo-zero-to-zero",
        contract_multiplier=1.0,
        entry_time_known=True,
        exit_time_known=True,
        contract_multiplier_known=True,
        notional_reliable=False,
        pnl_currency=cycle.quote_asset,
        side_known=True,
    )


def _normalize_crypto_fills(
    dataset: dict[str, Any],
    *,
    opening_zero_confirmed: bool,
    transfers_reconciled: bool,
) -> dict[str, Any]:
    provider = dataset["provider"]
    if provider not in {"pionex", "binance"}:
        return _result(False, "unsupported_provider_kind")
    if dataset.get("kind") != "fills" or dataset.get("market") != "spot":
        return _result(False, "unsupported_provider_kind")
    if not _private_provenance_verified(dataset):
        return _result(False, "authentication_unverified")
    rows = dataset["rows"]
    if not rows:
        return _result(False, "no_records_in_requested_window")
    if opening_zero_confirmed is not True:
        return _result(False, "opening_inventory_unconfirmed")
    if transfers_reconciled is not True:
        return _result(False, "transfers_unreconciled")

    try:
        fills = [_parse_fill(row, dataset["symbol"]) for row in rows]
        fills = _deduplicate_fills(fills, scope=_fill_scope(dataset))
    except ValueError as exc:
        if exc.args and exc.args[0] == "unsupported_fee_asset":
            return _result(False, "unsupported_fee_asset")
        if exc.args and exc.args[0] == "conflicting_fill_id":
            return _result(False, "conflicting_fill_id")
        return _result(False, "invalid_fill_rows")

    base_asset = fills[0].base_asset
    quote_asset = fills[0].quote_asset
    if any(
        fill.base_asset != base_asset or fill.quote_asset != quote_asset
        for fill in fills
    ):
        return _result(False, "inconsistent_assets")

    sides_by_time: dict[int, set[str]] = {}
    for fill in fills:
        sides_by_time.setdefault(fill.time_ms, set()).add(fill.side)
    if any(len(sides) > 1 for sides in sides_by_time.values()):
        return _result(False, "ambiguous_same_time_buy_sell")

    fills.sort(key=lambda item: item.time_ms)
    warnings: list[str] = ["cycle_notional_unreliable"]
    if any(fill.quote_mismatch for fill in fills):
        warnings.append("actual_quote_amount_differs_from_price_times_quantity")

    lots: list[_Lot] = []
    inventory = Decimal(0)
    current: _Cycle | None = None
    cycle_rows: list[dict[str, Any]] = []
    trades: list[Trade] = []

    for fill in fills:
        if current is None:
            if fill.side != "BUY":
                return _result(
                    False,
                    "sell_without_inventory",
                    cycles=cycle_rows,
                    warnings=warnings,
                )
            current = _Cycle(
                symbol=fill.symbol,
                base_asset=fill.base_asset,
                quote_asset=fill.quote_asset,
                start_ms=fill.time_ms,
            )

        current.fill_count += 1
        if fill.side == "BUY":
            acquired = fill.quantity
            cost = fill.quote_amount
            if fill.fee_amount > 0 and fill.fee_asset == fill.quote_asset:
                cost += fill.fee_amount
                current.fee_quote_equivalent += fill.fee_amount
            elif fill.fee_amount > 0 and fill.fee_asset == fill.base_asset:
                acquired -= fill.fee_amount
                if acquired <= 0:
                    return _result(False, "invalid_base_buy_fee", warnings=warnings)
                current.fee_quote_equivalent += fill.fee_amount * fill.price
                current.base_fee_seen = True
            lots.append(_Lot(quantity=acquired, cost=cost))
            inventory += acquired
            current.bought_quantity += fill.quantity
            current.acquired_quantity += acquired
            current.total_cost += cost
            continue

        consumed = fill.quantity
        proceeds = fill.quote_amount
        if fill.fee_amount > 0 and fill.fee_asset == fill.quote_asset:
            proceeds -= fill.fee_amount
            if proceeds < 0:
                return _result(False, "invalid_quote_sell_fee", warnings=warnings)
            current.fee_quote_equivalent += fill.fee_amount
        elif fill.fee_amount > 0 and fill.fee_asset == fill.base_asset:
            consumed += fill.fee_amount
            current.fee_quote_equivalent += fill.fee_amount * fill.price
            current.base_fee_seen = True
        if consumed > inventory:
            open_summary = _cycle_summary(
                current,
                status="open",
                remaining_quantity=inventory,
                remaining_cost=sum((lot.cost for lot in lots), Decimal(0)),
            )
            return _result(
                False,
                "insufficient_inventory",
                cycles=cycle_rows + [open_summary],
                unresolved_count=1,
                warnings=warnings,
            )
        allocated_cost = _consume_fifo(lots, consumed)
        inventory -= consumed
        current.sold_quantity += fill.quantity
        current.consumed_quantity += consumed
        current.total_proceeds += proceeds
        current.allocated_cost += allocated_cost
        current.realized_pnl += proceeds - allocated_cost

        if inventory == 0:
            current.end_ms = fill.time_ms
            if lots:
                return _result(False, "inventory_invariant_failed", warnings=warnings)
            cycle_rows.append(
                _cycle_summary(
                    current,
                    status="closed",
                    remaining_quantity=Decimal(0),
                    remaining_cost=Decimal(0),
                )
            )
            try:
                trades.append(_cycle_trade(current, provider))
            except (ValueError, OverflowError, OSError):
                return _result(False, "invalid_closed_cycle", warnings=warnings)
            if current.base_fee_seen and "base_fees_valued_at_fill_price_for_display" not in warnings:
                warnings.append("base_fees_valued_at_fill_price_for_display")
            current = None

    if current is not None or inventory != 0:
        if current is None:
            return _result(False, "inventory_invariant_failed", warnings=warnings)
        remaining_cost = sum((lot.cost for lot in lots), Decimal(0))
        cycle_rows.append(
            _cycle_summary(
                current,
                status="open",
                remaining_quantity=inventory,
                remaining_cost=remaining_cost,
            )
        )
        if current.base_fee_seen and "base_fees_valued_at_fill_price_for_display" not in warnings:
            warnings.append("base_fees_valued_at_fill_price_for_display")
        return _result(
            False,
            "open_inventory",
            cycles=cycle_rows,
            unresolved_count=1,
            warnings=warnings,
        )

    if not trades:
        return _result(
            False,
            "no_closed_cycles",
            cycles=cycle_rows,
            warnings=warnings,
        )
    log = TradeLog(
        trades=trades,
        source=f"api:{provider}:fills",
        account_label=f"{provider}:spot",
    )
    return _result(True, "ready", log=log, cycles=cycle_rows, warnings=warnings)


def _currency_code(value: Any) -> str:
    if not isinstance(value, str) or _CURRENCY.fullmatch(value) is None:
        raise ValueError("currency")
    # NTD is a source-declared alternate label for the same settlement unit;
    # it is never inferred when the source supplied no currency at all.
    return "TWD" if value == "NTD" else value


def _source_settlement_currencies(source: dict[str, Any]) -> set[str]:
    currencies: set[str] = set()
    for field_name in ("currency", "pnl_currency"):
        if field_name in source:
            currencies.add(_currency_code(source[field_name]))
    return currencies


def _shioaji_settlement_currency(row: dict[str, Any]) -> str:
    details = row["details"]
    if not isinstance(details, list):
        raise ValueError("details")

    sources = [row]
    currencies = _source_settlement_currencies(row)
    for detail in details:
        if not isinstance(detail, dict):
            raise ValueError("details")
        sources.append(detail)
        currencies.update(_source_settlement_currencies(detail))

    if not currencies:
        raise ValueError("settlement_currency_unavailable")
    if len(currencies) != 1:
        raise ValueError("mixed_settlement_currencies")
    settlement_currency = next(iter(currencies))

    # The row-level settlement declaration is the explicit same-unit contract.
    # If a source separately labels fee or tax in another unit, no FX rate may
    # be invented to combine it with realized P&L.
    for source in sources:
        for field_name in ("fee_currency", "tax_currency"):
            if (
                field_name in source
                and _currency_code(source[field_name]) != settlement_currency
            ):
                raise ValueError("fee_tax_currency_unconfirmed")
    return settlement_currency


def _taipei_placeholder(local_date: date) -> datetime:
    if (
        _TAIPEI_IS_FIXED_FALLBACK
        and local_date < _TAIPEI_FIXED_OFFSET_MIN_DATE
    ):
        raise _TaipeiFallbackHistoryUnavailable()
    return datetime.combine(local_date, datetime_time.min).replace(tzinfo=_TAIPEI)


_SHIOAJI_COST_FIELDS = ("fee", "tax", "interest", "shortselling_fee")
_SHIOAJI_COST_ALIASES = {
    "fee": ("fee", "handling_fee", "commission"),
    "tax": ("tax", "trade_tax"),
    "interest": ("interest", "financing_interest"),
    "shortselling_fee": ("shortselling_fee", "short_selling_fee"),
}


def _source_costs(row: dict[str, Any]) -> tuple[dict[str, Decimal], set[str]]:
    """Read source-declared totals without turning absent values into zero."""
    values: dict[str, Decimal] = {}
    present: set[str] = set()
    for field_name in _SHIOAJI_COST_FIELDS:
        names = _SHIOAJI_COST_ALIASES[field_name]
        source_name = next((name for name in names if name in row), None)
        if source_name is None:
            continue
        present.add(field_name)
        raw = row[source_name]
        if raw is None:
            raise ValueError("source_costs_unavailable")
        values[field_name] = _decimal(raw)
    return values, present


def _detail_costs(details: list[Any]) -> dict[str, Decimal]:
    observed: dict[str, Decimal] = {}
    for detail in details:
        if not isinstance(detail, dict):
            raise ValueError("details")
        for field_name in _SHIOAJI_COST_FIELDS:
            names = _SHIOAJI_COST_ALIASES[field_name]
            source_name = next((name for name in names if name in detail), None)
            if source_name is None or detail[source_name] is None:
                continue
            value = _decimal(detail[source_name])
            observed[field_name] = observed.get(field_name, Decimal(0)) + value
    return observed


def _merge_observed_detail_costs(
    details: list[Any], observed: dict[str, Decimal], raw: Any
) -> dict[str, Decimal]:
    if not isinstance(raw, dict):
        return observed
    merged = dict(observed)
    for field_name in _SHIOAJI_COST_FIELDS:
        if field_name in merged or field_name not in raw or raw[field_name] is None:
            continue
        merged[field_name] = _decimal(raw[field_name])
    return merged


def _normalize_shioaji_realizations(
    dataset: dict[str, Any],
    *,
    pnl_basis: str,
    total_costs_confirmed: bool,
) -> dict[str, Any]:
    if dataset.get("provider") != "shioaji" or dataset.get("kind") != "realizations":
        return _result(False, "unsupported_provider_kind")
    market_name = dataset.get("market")
    if market_name not in {"stock", "futures"}:
        return _result(False, "unsupported_provider_kind")
    if not _private_provenance_verified(dataset):
        return _result(False, "authentication_unverified")
    rows = dataset["rows"]
    if not rows:
        return _result(False, "no_records_in_requested_window")
    if not isinstance(pnl_basis, str) or pnl_basis not in {"gross", "net"}:
        return _result(False, "pnl_basis_unconfirmed")
    if total_costs_confirmed is not True:
        return _result(False, "total_costs_unconfirmed")

    market = Market.TW_STOCK if market_name == "stock" else Market.TW_FUTURES
    required_unit = "Share" if market_name == "stock" else "Common"
    trades: list[Trade] = []
    summaries: list[dict[str, Any]] = []
    dataset_currency: str | None = None
    try:
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("row")
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or symbol != dataset["symbol"]:
                raise ValueError("symbol")
            if row.get("market") != market_name or row.get("unit") != required_unit:
                raise ValueError("market_unit")
            if row.get("time_precision") != "date":
                raise ValueError("time_precision")
            raw_date = row.get("date")
            if not isinstance(raw_date, str):
                raise ValueError("date")
            local_date = date.fromisoformat(raw_date)
            if local_date.isoformat() != raw_date:
                raise ValueError("date")
            if not isinstance(row.get("details"), list):
                raise ValueError("details")
            settlement_currency = _shioaji_settlement_currency(row)
            if dataset_currency is None:
                dataset_currency = settlement_currency
            elif settlement_currency != dataset_currency:
                raise ValueError("mixed_settlement_currencies")
            quantity = _decimal(row.get("quantity"))
            reported_pnl = _decimal(row.get("pnl"), signed=True)
            try:
                source_costs, source_cost_fields = _source_costs(row)
                detail_costs = _merge_observed_detail_costs(
                    row["details"],
                    _detail_costs(row["details"]),
                    row.get("observed_detail_costs"),
                )
            except (ValueError, TypeError, OverflowError):
                raise ValueError("source_costs_unavailable") from None
            fee = source_costs.get("fee")
            tax = source_costs.get("tax")
            if pnl_basis == "gross" and not {"fee", "tax"}.issubset(source_cost_fields):
                return _result(False, "source_costs_unavailable")

            uncovered_detail_costs = {
                field_name
                for field_name, detail_value in detail_costs.items()
                if detail_value > 0
                and (
                    field_name not in source_costs
                    or source_costs[field_name] < detail_value
                )
            }
            if pnl_basis == "gross" and uncovered_detail_costs:
                return _result(False, "source_costs_unavailable")

            display_costs = {
                field_name: source_costs.get(field_name, detail_costs.get(field_name, Decimal(0)))
                for field_name in _SHIOAJI_COST_FIELDS
            }
            total_cost = sum(display_costs.values(), Decimal(0))
            if quantity <= 0:
                raise ValueError("quantity")
            net_pnl = reported_pnl - total_cost if pnl_basis == "gross" else reported_pnl

            entry_present = row.get("entry_price") is not None
            exit_present = row.get("exit_price") is not None
            entry_price = _decimal(row["entry_price"]) if entry_present else Decimal(0)
            exit_price = _decimal(row["exit_price"]) if exit_present else Decimal(0)
            if entry_present and entry_price <= 0:
                raise ValueError("entry_price")
            if exit_present and exit_price <= 0:
                raise ValueError("exit_price")
            placeholder = _taipei_placeholder(local_date)
            reliable_notional = (
                market == Market.TW_STOCK
                and entry_present
                and exit_present
                and entry_price > 0
                and exit_price > 0
            )
            trade = Trade(
                symbol=symbol,
                market=market,
                side=Side.LONG,
                entry_time=placeholder,
                exit_time=placeholder,
                entry_price=_finite_float(entry_price),
                exit_price=_finite_float(exit_price),
                quantity=_finite_float(quantity),
                 fees=_finite_float(total_cost),
                pnl=_finite_float(net_pnl),
                tag=f"api:shioaji:realization:{pnl_basis}",
                contract_multiplier=1.0,
                entry_time_known=False,
                exit_time_known=False,
                contract_multiplier_known=market == Market.TW_STOCK,
                notional_reliable=reliable_notional,
                pnl_currency=settlement_currency,
                side_known=False,
            )
            trades.append(trade)
            summaries.append(
                {
                    "status": "closed",
                    "symbol": symbol,
                    "market": market_name,
                    "date": raw_date,
                    "time_precision": "date",
                    "quantity": _decimal_text(quantity),
                    "reported_pnl": _decimal_text(reported_pnl),
                    "pnl_basis": pnl_basis,
                     "fee": _decimal_text(display_costs["fee"]),
                     "tax": _decimal_text(display_costs["tax"]),
                     "interest": _decimal_text(display_costs["interest"]),
                     "shortselling_fee": _decimal_text(display_costs["shortselling_fee"]),
                     "costs_known": not uncovered_detail_costs and {"fee", "tax"}.issubset(source_cost_fields),
                     "cost_scope": row.get(
                         "cost_scope",
                         "source_reported_totals_requires_confirmation"
                         if source_cost_fields
                         else "detail_components_unknown_total",
                     ),
                     "net_pnl": _decimal_text(net_pnl),
                     "currency": settlement_currency,
                 }
            )
    except _TaipeiFallbackHistoryUnavailable:
        return _result(False, "taipei_timezone_history_unavailable")
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        if exc.args and exc.args[0] in {
            "settlement_currency_unavailable",
            "mixed_settlement_currencies",
            "fee_tax_currency_unconfirmed",
        }:
            return _result(False, exc.args[0])
        return _result(False, "invalid_realization_rows")

    log = TradeLog(
        trades=trades,
        source="api:shioaji:realizations",
        account_label=f"shioaji:{market_name}",
    )
    return _result(
        True,
        "ready",
        log=log,
        cycles=summaries,
        warnings=[
            "date_only_timestamps_not_used_for_intraday_or_ordering",
            f"pnl_basis_confirmed:{pnl_basis}",
        ],
    )


def normalize_closed_dataset(
    dataset: Any,
    *,
    opening_zero_confirmed: bool = False,
    transfers_reconciled: bool = False,
    pnl_basis: str = "unknown",
    total_costs_confirmed: bool = False,
) -> dict[str, Any]:
    """Build a ``TradeLog`` only when the closed-trade basis is complete.

    Partial inventory, uncertain opening balances, incomplete pagination,
    unsupported fee assets, and unconfirmed Shioaji P&L basis all fail closed.
    """

    if not _dataset_is_bounded(dataset) or not _valid_envelope(dataset):
        return _result(False, "invalid_dataset")
    if not _coverage_complete(
        dataset,
        allow_exact_fill_duplicates=(
            dataset["provider"] in {"pionex", "binance"}
            and dataset["kind"] == "fills"
        ),
    ):
        return _result(False, "coverage_incomplete")
    if dataset["provider"] in {"pionex", "binance"}:
        return _normalize_crypto_fills(
            dataset,
            opening_zero_confirmed=opening_zero_confirmed,
            transfers_reconciled=transfers_reconciled,
        )
    if dataset["provider"] == "shioaji":
        return _normalize_shioaji_realizations(
            dataset,
            pnl_basis=pnl_basis,
            total_costs_confirmed=total_costs_confirmed,
        )
    return _result(False, "unsupported_provider_kind")
