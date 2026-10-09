"""Bounded read-only Pionex SPOT market/account adapter."""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from core.data_api.context import SyncContext
from core.data_api.credentials import CredentialPair
from core.data_api.models import validate_query
from core.data_api.providers import (
    INTERVAL_MS,
    MAX_PAGES,
    MAX_ROWS,
    _add_reason,
    _calculated_decimal,
    _context_now_ms,
    _coverage,
    _credential_values,
    _dataset,
    _decimal_text,
    _id_text,
    _missing_crypto_bars,
    _strict_int,
)
from core.data_api.transport import DataAPIError, ReadOnlyHTTPTransport

_HOST = "api.pionex.com"
_KLINES_PATH = "/api/v1/market/klines"
_FILLS_PATH = "/api/v1/trade/fills"
_BALANCES_PATH = "/api/v1/account/balances"

_INTERVALS = {
    "1m": "1M",
    "5m": "5M",
    "15m": "15M",
    "30m": "30M",
    "1h": "60M",
    "4h": "4H",
    "1d": "1D",
}


def _envelope_data(payload: Any, *, private: bool = False) -> Any:
    if not isinstance(payload, Mapping):
        raise DataAPIError("invalid_response")
    result = payload.get("result")
    if result is False:
        code = payload.get("code")
        if private and str(code) in {"-2014", "-2015", "401", "403", "AUTH_FAILED"}:
            raise DataAPIError("auth")
        if str(code) in {"429", "TOO_MANY_REQUESTS", "RATE_LIMIT"}:
            raise DataAPIError("rate_limit")
        raise DataAPIError("invalid_response")
    return payload.get("data", payload)


def _list_field(payload: Any, names: tuple[str, ...], *, private: bool = False) -> list[Any]:
    data = _envelope_data(payload, private=private)
    if isinstance(data, list):
        return data
    if not isinstance(data, Mapping):
        raise DataAPIError("invalid_response")
    for name in names:
        value = data.get(name)
        if isinstance(value, list):
            return value
    raise DataAPIError("invalid_response")


def _field(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    raise ValueError("field")


def _optional_field(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _parse_candle(raw: Any, captured_ms: int, interval_ms: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("candle")
    time_ms = _strict_int(_field(raw, "time", "openTime", "timestamp"), minimum=0)
    open_text = _decimal_text(_field(raw, "open"), positive=True)
    high_text = _decimal_text(_field(raw, "high"), positive=True)
    low_text = _decimal_text(_field(raw, "low"), positive=True)
    close_text = _decimal_text(_field(raw, "close"), positive=True)
    volume_text = _decimal_text(_field(raw, "volume"), nonnegative=True)
    open_value = Decimal(open_text)
    high_value = Decimal(high_text)
    low_value = Decimal(low_text)
    close_value = Decimal(close_text)
    if high_value < max(open_value, low_value, close_value):
        raise ValueError("candle")
    if low_value > min(open_value, high_value, close_value):
        raise ValueError("candle")
    return {
        "time_ms": time_ms,
        "open": open_text,
        "high": high_text,
        "low": low_text,
        "close": close_text,
        "volume": volume_text,
        "is_closed": time_ms + interval_ms <= captured_ms,
    }


def _assets(symbol: str) -> tuple[str, str]:
    parts = symbol.split("_")
    if len(parts) != 2 or not all(parts):
        raise DataAPIError("invalid_response")
    return parts[0], parts[1]


def _parse_fill(raw: Any, symbol: str) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw, Mapping):
        raise ValueError("fill")
    raw_symbol = _optional_field(raw, "symbol")
    if raw_symbol is not None and raw_symbol != symbol:
        raise ValueError("fill")
    base_asset, quote_asset = _assets(symbol)
    side_value = _field(raw, "side")
    if not isinstance(side_value, str) or side_value.upper() not in {"BUY", "SELL"}:
        raise ValueError("fill")
    price = _decimal_text(_field(raw, "price"), positive=True)
    quantity = _decimal_text(
        _field(raw, "size", "filledSize", "quantity", "qty"),
        positive=True,
    )
    quote_raw = _optional_field(raw, "amount", "filledAmount", "quoteAmount", "quoteQty")
    quote_calculated = quote_raw is None
    if quote_raw is None:
        quote_amount = _calculated_decimal(Decimal(price) * Decimal(quantity))
    else:
        quote_amount = _decimal_text(quote_raw, nonnegative=True)
    fee_raw = _optional_field(raw, "fee", "feeAmount", "commission")
    if fee_raw is None:
        raise ValueError("fill")
    fee_amount = _decimal_text(fee_raw, nonnegative=True)
    fee_asset_raw = _optional_field(raw, "feeCoin", "feeAsset", "commissionAsset")
    if fee_asset_raw is None:
        if Decimal(fee_amount) > 0:
            raise ValueError("fill")
        fee_asset = None
    elif isinstance(fee_asset_raw, str) and fee_asset_raw and len(fee_asset_raw) <= 32:
        fee_asset = fee_asset_raw
    elif Decimal(fee_amount) == 0 and isinstance(fee_asset_raw, str) and not fee_asset_raw:
        fee_asset = None
    else:
        raise ValueError("fill")
    return (
        {
            "id": _id_text(_field(raw, "id", "fillId", "tradeId")),
            "order_id": _id_text(_field(raw, "orderId", "order_id")),
            "side": side_value.upper(),
            "time_ms": _strict_int(
                _field(raw, "timestamp", "time", "createTime"),
                minimum=0,
            ),
            "symbol": symbol,
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "quantity": quantity,
            "quote_amount": quote_amount,
            "price": price,
            "fee_amount": fee_amount,
            "fee_asset": fee_asset,
        },
        quote_calculated,
    )


class PionexDataClient:
    """Pionex public candles and authenticated read-only history/balances."""

    provider = "pionex"

    def __init__(
        self,
        *,
        transport: ReadOnlyHTTPTransport | None = None,
        credentials: CredentialPair | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._transport = transport if transport is not None else ReadOnlyHTTPTransport()
        self._credentials = credentials
        self._clock_ms = clock_ms if clock_ms is not None else lambda: time.time_ns() // 1_000_000
        if not callable(self._clock_ms):
            raise ValueError("clock_ms")

    def fetch(self, kind: str, query: Mapping[str, Any], context: SyncContext) -> dict[str, Any]:
        context.check()
        validated = validate_query(self.provider, kind, query)
        if kind == "candles":
            return self.fetch_candles(validated, context, _validated=True)
        if kind == "fills":
            return self.fetch_fills(validated, context, _validated=True)
        if kind == "account":
            return self.fetch_account(validated, context, _validated=True)
        raise ValueError("kind")

    def fetch_candles(
        self,
        query: Mapping[str, Any],
        context: SyncContext,
        *,
        _validated: bool = False,
    ) -> dict[str, Any]:
        validated = dict(query) if _validated else validate_query(self.provider, "candles", query)
        context.check()
        captured_ms = _context_now_ms(context)
        symbol = validated["symbol"]
        interval = validated["interval"]
        interval_ms = INTERVAL_MS[interval]
        start_ms = validated["start_ms"]
        end_ms = validated["end_ms"]
        requested_limit = min(validated["limit"], MAX_ROWS)
        cursor_end = end_ms
        pages = 0
        raw_count = 0
        rejected = 0
        duplicates = 0
        reasons: list[str] = []
        by_time: dict[int, dict[str, Any]] = {}
        boundary_complete = False
        pages_complete = True
        truncated = False

        while pages < MAX_PAGES:
            context.check()
            remaining = requested_limit - len(by_time)
            if remaining <= 0:
                if by_time and min(by_time) <= start_ms:
                    boundary_complete = True
                else:
                    truncated = True
                    _add_reason(reasons, "row_limit")
                break
            page_limit = min(500, remaining)
            payload = self._transport.get_json(
                _HOST,
                _KLINES_PATH,
                {
                    "endTime": cursor_end,
                    "interval": _INTERVALS[interval],
                    "limit": page_limit,
                    "symbol": symbol,
                },
                cancel_event=context.cancel_event,
            )
            raw_rows = _list_field(payload, ("klines", "data"))
            pages += 1
            raw_count += len(raw_rows)
            context.advance(pages=1, records=len(raw_rows))
            if not raw_rows:
                boundary_complete = True
                break

            seen_times: list[int] = []
            for raw in raw_rows:
                try:
                    parsed = _parse_candle(raw, captured_ms, interval_ms)
                    time_ms = parsed["time_ms"]
                    seen_times.append(time_ms)
                    if time_ms < start_ms or time_ms > end_ms:
                        continue
                    previous = by_time.get(time_ms)
                    if previous is None:
                        by_time[time_ms] = parsed
                    elif previous == parsed:
                        duplicates += 1
                    else:
                        duplicates += 1
                        rejected += 1
                        _add_reason(reasons, "conflicting_timestamp")
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    rejected += 1
                    _add_reason(reasons, "rejected_rows")

            if not seen_times:
                pages_complete = False
                boundary_complete = False
                _add_reason(reasons, "pagination_no_progress")
                break
            oldest = min(seen_times)
            if oldest <= start_ms:
                boundary_complete = True
                break
            if len(raw_rows) < page_limit:
                boundary_complete = True
                break
            next_end = oldest - 1
            if next_end >= cursor_end:
                pages_complete = False
                boundary_complete = False
                _add_reason(reasons, "pagination_no_progress")
                break
            cursor_end = next_end
        else:
            pages_complete = False
            boundary_complete = False
            truncated = True
            _add_reason(reasons, "page_limit")

        rows = [by_time[key] for key in sorted(by_time)]
        if len(rows) > requested_limit:
            rows = rows[-requested_limit:]
            truncated = True
            _add_reason(reasons, "row_limit")
        missing = _missing_crypto_bars(rows, interval_ms)
        if missing:
            _add_reason(reasons, "missing_bars")
        if duplicates:
            _add_reason(reasons, "duplicate_rows")
        complete = (
            pages_complete
            and boundary_complete
            and not truncated
            and rejected == 0
            and duplicates == 0
            and missing == 0
        )
        coverage = _coverage(
            complete=complete,
            pages_complete=pages_complete,
            boundary_complete=boundary_complete,
            truncated=truncated,
            rejected_rows=rejected,
            duplicate_rows=duplicates,
            missing_bars=missing,
            reasons=reasons,
            rows=rows,
            raw_count=raw_count,
        )
        return _dataset(
            provider=self.provider,
            kind="candles",
            market="spot",
            symbol=symbol,
            captured_ms=captured_ms,
            timezone_name="UTC",
            query=validated,
            rows=rows,
            coverage=coverage,
            summary={
                "row_count": len(rows),
                "latest_bar_time_ms": rows[-1]["time_ms"] if rows else None,
            },
            data_type="public-market-data",
            authentication_verified=False,
        )

    def fetch_fills(
        self,
        query: Mapping[str, Any],
        context: SyncContext,
        *,
        _validated: bool = False,
    ) -> dict[str, Any]:
        validated = dict(query) if _validated else validate_query(self.provider, "fills", query)
        context.check()
        captured_ms = _context_now_ms(context)
        symbol = validated["symbol"]
        start_ms = validated["start_ms"]
        end_ms = validated["end_ms"]
        requested_limit = min(validated["limit"], MAX_ROWS)
        pending: list[tuple[int, int]] = [(start_ms, end_ms)]
        pages = 0
        raw_count = 0
        rejected = 0
        duplicates = 0
        calculated_quotes = 0
        reasons: list[str] = []
        by_id: dict[str, dict[str, Any]] = {}
        boundary_complete = True
        truncated = False

        while pending and pages < MAX_PAGES:
            context.check()
            window_start, window_end = pending.pop(0)
            payload = self._private_get(
                _FILLS_PATH,
                {
                    "endTime": window_end,
                    "startTime": window_start,
                    "symbol": symbol,
                },
                context,
            )
            raw_rows = _list_field(payload, ("fills", "trades"), private=True)
            pages += 1
            raw_count += len(raw_rows)
            context.advance(pages=1, records=len(raw_rows))
            context.sleep(0.55)

            saturated = len(raw_rows) >= 100
            if saturated and window_start != window_end:
                midpoint = window_start + (window_end - window_start) // 2
                pending.append((window_start, midpoint))
                pending.append((midpoint + 1, window_end))
                continue

            for raw in raw_rows:
                try:
                    parsed, calculated = _parse_fill(raw, symbol)
                    if parsed["time_ms"] < start_ms or parsed["time_ms"] > end_ms:
                        continue
                    previous = by_id.get(parsed["id"])
                    if previous is None:
                        by_id[parsed["id"]] = parsed
                        calculated_quotes += int(calculated)
                    elif previous == parsed:
                        duplicates += 1
                    else:
                        duplicates += 1
                        rejected += 1
                        _add_reason(reasons, "conflicting_fill_id")
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    rejected += 1
                    _add_reason(reasons, "rejected_rows")

            if saturated:
                boundary_complete = False
                truncated = True
                _add_reason(reasons, "saturated_one_ms_window")

        pages_complete = not pending
        if pending:
            boundary_complete = False
            truncated = True
            _add_reason(reasons, "page_limit")

        rows = sorted(by_id.values(), key=lambda row: (row["time_ms"], row["id"]))
        if len(rows) > requested_limit:
            rows = rows[-requested_limit:]
            truncated = True
            _add_reason(reasons, "row_limit")
        if duplicates:
            _add_reason(reasons, "duplicate_rows")
        complete = (
            pages_complete
            and boundary_complete
            and not truncated
            and rejected == 0
            and duplicates == 0
        )
        coverage = _coverage(
            complete=complete,
            pages_complete=pages_complete,
            boundary_complete=boundary_complete,
            truncated=truncated,
            rejected_rows=rejected,
            duplicate_rows=duplicates,
            missing_bars=0,
            reasons=reasons,
            rows=rows,
            raw_count=raw_count,
        )
        return _dataset(
            provider=self.provider,
            kind="fills",
            market="spot",
            symbol=symbol,
            captured_ms=captured_ms,
            timezone_name="UTC",
            query=validated,
            rows=rows,
            coverage=coverage,
            summary={
                "row_count": len(rows),
                "quote_amount_calculated_rows": calculated_quotes,
            },
            data_type="private-transaction-data",
            authentication_verified=True,
        )

    def fetch_account(
        self,
        query: Mapping[str, Any],
        context: SyncContext,
        *,
        _validated: bool = False,
    ) -> dict[str, Any]:
        validated = dict(query) if _validated else validate_query(self.provider, "account", query)
        context.check()
        captured_ms = _context_now_ms(context)
        payload = self._private_get(_BALANCES_PATH, {}, context)
        raw_rows = _list_field(payload, ("balances", "coins"), private=True)
        context.advance(pages=1, records=len(raw_rows))
        rejected = 0
        duplicates = 0
        reasons: list[str] = []
        balances_by_asset: dict[str, dict[str, str]] = {}
        for raw in raw_rows:
            try:
                if not isinstance(raw, Mapping):
                    raise ValueError("balance")
                asset = _field(raw, "coin", "asset")
                if not isinstance(asset, str) or not asset or len(asset) > 32:
                    raise ValueError("balance")
                balance = {
                    "asset": asset,
                    "free": _decimal_text(_field(raw, "free", "available"), nonnegative=True),
                    "locked": _decimal_text(
                        _field(raw, "frozen", "locked"),
                        nonnegative=True,
                    ),
                }
                previous = balances_by_asset.get(asset)
                if previous is None:
                    balances_by_asset[asset] = balance
                elif previous == balance:
                    duplicates += 1
                else:
                    duplicates += 1
                    rejected += 1
                    _add_reason(reasons, "conflicting_asset_balance")
            except (KeyError, TypeError, ValueError, InvalidOperation):
                rejected += 1
                _add_reason(reasons, "rejected_rows")
        balances = [balances_by_asset[key] for key in sorted(balances_by_asset)]
        if duplicates:
            _add_reason(reasons, "duplicate_rows")
        complete = rejected == 0 and duplicates == 0
        coverage = _coverage(
            complete=complete,
            pages_complete=True,
            boundary_complete=True,
            truncated=False,
            rejected_rows=rejected,
            duplicate_rows=duplicates,
            missing_bars=0,
            reasons=reasons,
            rows=[],
            raw_count=len(raw_rows),
        )
        return _dataset(
            provider=self.provider,
            kind="account",
            market="spot",
            symbol=validated.get("symbol", ""),
            captured_ms=captured_ms,
            timezone_name="UTC",
            query=validated,
            rows=[],
            coverage=coverage,
            summary={"balances": balances},
            data_type="private-account-data",
            authentication_verified=True,
        )

    def _private_get(
        self,
        path: str,
        params: Mapping[str, Any],
        context: SyncContext,
    ) -> Any:
        api_key, api_secret = _credential_values(self._credentials)
        signed = dict(params)
        timestamp = self._clock_ms()
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise DataAPIError("invalid_response")
        signed["timestamp"] = timestamp
        items = sorted(signed.items(), key=lambda item: item[0])
        canonical = "&".join(f"{key}={value}" for key, value in items)
        payload = f"GET{path}?{canonical}".encode("utf-8")
        signature = hmac.new(api_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        ordered_params = {key: value for key, value in items}
        return self._transport.get_json(
            _HOST,
            path,
            ordered_params,
            {
                "PIONEX-KEY": api_key,
                "PIONEX-SIGNATURE": signature,
            },
            cancel_event=context.cancel_event,
        )


__all__ = ("PionexDataClient",)
