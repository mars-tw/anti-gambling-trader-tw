"""Bounded read-only Binance SPOT market/account adapter."""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
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

_PUBLIC_HOST = "data-api.binance.vision"
_PRIVATE_HOST = "api.binance.com"
_KLINES_PATH = "/api/v3/klines"
_EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"
_TIME_PATH = "/api/v3/time"
_TRADES_PATH = "/api/v3/myTrades"
_ACCOUNT_PATH = "/api/v3/account"
_DAY_MS = 24 * 60 * 60 * 1000


def _api_error(payload: Any) -> None:
    if isinstance(payload, Mapping) and "code" in payload and "msg" in payload:
        code = payload.get("code")
        if code in (-2014, -2015) or str(code) in {"-2014", "-2015"}:
            raise DataAPIError("auth")
        if code in (-1003, -1015) or str(code) in {"-1003", "-1015"}:
            raise DataAPIError("rate_limit")
        raise DataAPIError("invalid_response")


def _expect_list(payload: Any) -> list[Any]:
    _api_error(payload)
    if not isinstance(payload, list):
        raise DataAPIError("invalid_response")
    return payload


def _expect_mapping(payload: Any) -> Mapping[str, Any]:
    _api_error(payload)
    if not isinstance(payload, Mapping):
        raise DataAPIError("invalid_response")
    return payload


def _parse_kline(raw: Any, captured_ms: int, interval_ms: int) -> dict[str, Any]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 6:
        raise ValueError("kline")
    time_ms = _strict_int(raw[0], minimum=0)
    open_text = _decimal_text(raw[1], positive=True)
    high_text = _decimal_text(raw[2], positive=True)
    low_text = _decimal_text(raw[3], positive=True)
    close_text = _decimal_text(raw[4], positive=True)
    volume_text = _decimal_text(raw[5], nonnegative=True)
    open_value = Decimal(open_text)
    high_value = Decimal(high_text)
    low_value = Decimal(low_text)
    close_value = Decimal(close_text)
    if high_value < max(open_value, low_value, close_value):
        raise ValueError("kline")
    if low_value > min(open_value, high_value, close_value):
        raise ValueError("kline")
    return {
        "time_ms": time_ms,
        "open": open_text,
        "high": high_text,
        "low": low_text,
        "close": close_text,
        "volume": volume_text,
        "is_closed": time_ms + interval_ms <= captured_ms,
    }


def _parse_trade(
    raw: Any,
    symbol: str,
    base_asset: str,
    quote_asset: str,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw, Mapping):
        raise ValueError("trade")
    raw_symbol = raw.get("symbol")
    if raw_symbol is not None and raw_symbol != symbol:
        raise ValueError("trade")
    buyer = raw.get("isBuyer")
    if not isinstance(buyer, bool):
        raise ValueError("trade")
    price = _decimal_text(raw.get("price"), positive=True)
    quantity = _decimal_text(raw.get("qty"), positive=True)
    quote_raw = raw.get("quoteQty")
    calculated = quote_raw is None
    quote_amount = (
        _calculated_decimal(Decimal(price) * Decimal(quantity))
        if quote_raw is None
        else _decimal_text(quote_raw, nonnegative=True)
    )
    fee_asset = raw.get("commissionAsset")
    if not isinstance(fee_asset, str) or not fee_asset or len(fee_asset) > 32:
        raise ValueError("trade")
    return (
        {
            "id": _id_text(raw.get("id")),
            "order_id": _id_text(raw.get("orderId")),
            "side": "BUY" if buyer else "SELL",
            "time_ms": _strict_int(raw.get("time"), minimum=0),
            "symbol": symbol,
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "quantity": quantity,
            "quote_amount": quote_amount,
            "price": price,
            "fee_amount": _decimal_text(raw.get("commission"), nonnegative=True),
            "fee_asset": fee_asset,
        },
        calculated,
    )


def _trade_id(raw: Any) -> int:
    if not isinstance(raw, Mapping):
        raise ValueError("trade id")
    return _strict_int(raw.get("id"), minimum=0)


def _trade_time(raw: Any) -> int:
    if not isinstance(raw, Mapping):
        raise ValueError("trade time")
    return _strict_int(raw.get("time"), minimum=0)


class BinanceDataClient:
    """Binance public candles and authenticated read-only history/balances."""

    provider = "binance"

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
        cursor = start_ms
        pages = 0
        raw_count = 0
        rejected = 0
        duplicates = 0
        reasons: list[str] = []
        by_time: dict[int, dict[str, Any]] = {}
        pages_complete = True
        boundary_complete = False
        truncated = False

        while cursor <= end_ms and pages < MAX_PAGES:
            context.check()
            remaining = requested_limit - len(by_time)
            if remaining <= 0:
                truncated = True
                _add_reason(reasons, "row_limit")
                break
            page_limit = min(1000, remaining)
            payload = self._transport.get_json(
                _PUBLIC_HOST,
                _KLINES_PATH,
                {
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": page_limit,
                },
                cancel_event=context.cancel_event,
            )
            raw_rows = _expect_list(payload)
            pages += 1
            raw_count += len(raw_rows)
            context.advance(pages=1, records=len(raw_rows))
            if not raw_rows:
                boundary_complete = True
                break
            page_times: list[int] = []
            for raw in raw_rows:
                try:
                    parsed = _parse_kline(raw, captured_ms, interval_ms)
                    time_ms = parsed["time_ms"]
                    page_times.append(time_ms)
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
                except (IndexError, KeyError, TypeError, ValueError, InvalidOperation):
                    rejected += 1
                    _add_reason(reasons, "rejected_rows")
            if not page_times:
                pages_complete = False
                _add_reason(reasons, "pagination_no_progress")
                break
            next_cursor = max(page_times) + interval_ms
            if next_cursor <= cursor:
                pages_complete = False
                _add_reason(reasons, "pagination_no_progress")
                break
            if next_cursor > end_ms or len(raw_rows) < page_limit:
                boundary_complete = True
                break
            cursor = next_cursor
        else:
            if cursor > end_ms:
                boundary_complete = True
            else:
                pages_complete = False
                truncated = True
                _add_reason(reasons, "page_limit")

        rows = [by_time[key] for key in sorted(by_time)]
        if len(rows) > requested_limit:
            rows = rows[:requested_limit]
            truncated = True
            _add_reason(reasons, "row_limit")
        if rows and rows[-1]["time_ms"] + interval_ms > end_ms:
            boundary_complete = True
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
        request_count = 0
        offset_ms = self._server_offset(context)
        request_count += 1
        base_asset, quote_asset = self._symbol_assets(symbol, context)
        request_count += 1
        by_id: dict[str, dict[str, Any]] = {}
        raw_count = 0
        rejected = 0
        duplicates = 0
        calculated_quotes = 0
        reasons: list[str] = []
        pages_complete = True
        boundary_complete = True
        truncated = False
        slice_start = start_ms

        def consume(raw_rows: list[Any], lower_ms: int, upper_ms: int) -> None:
            nonlocal rejected, duplicates, calculated_quotes
            for raw in raw_rows:
                try:
                    parsed, calculated = _parse_trade(raw, symbol, base_asset, quote_asset)
                    if parsed["time_ms"] < lower_ms or parsed["time_ms"] > upper_ms:
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

        while slice_start <= end_ms:
            context.check()
            if request_count >= MAX_PAGES:
                pages_complete = False
                boundary_complete = False
                truncated = True
                _add_reason(reasons, "page_limit")
                break
            slice_end = min(end_ms, slice_start + _DAY_MS - 1)
            payload = self._private_get(
                _TRADES_PATH,
                {
                    "symbol": symbol,
                    "startTime": slice_start,
                    "endTime": slice_end,
                    "limit": 1000,
                },
                context,
                offset_ms,
            )
            raw_rows = _expect_list(payload)
            request_count += 1
            raw_count += len(raw_rows)
            context.advance(pages=1, records=len(raw_rows))
            consume(raw_rows, slice_start, slice_end)

            if len(raw_rows) >= 1000:
                try:
                    last_actual_id = max(_trade_id(raw) for raw in raw_rows)
                except (TypeError, ValueError):
                    boundary_complete = False
                    _add_reason(reasons, "pagination_no_progress")
                    break
                from_id = last_actual_id + 1
                slice_done = False
                while not slice_done:
                    context.check()
                    if request_count >= MAX_PAGES:
                        pages_complete = False
                        boundary_complete = False
                        truncated = True
                        _add_reason(reasons, "page_limit")
                        break
                    continuation = self._private_get(
                        _TRADES_PATH,
                        {
                            "symbol": symbol,
                            "fromId": from_id,
                            "limit": 1000,
                        },
                        context,
                        offset_ms,
                    )
                    more_rows = _expect_list(continuation)
                    request_count += 1
                    raw_count += len(more_rows)
                    context.advance(pages=1, records=len(more_rows))
                    consume(more_rows, slice_start, slice_end)
                    if not more_rows:
                        slice_done = True
                        continue
                    try:
                        actual_ids = [_trade_id(raw) for raw in more_rows]
                        actual_times = [_trade_time(raw) for raw in more_rows]
                    except (TypeError, ValueError):
                        boundary_complete = False
                        _add_reason(reasons, "pagination_no_progress")
                        break
                    next_actual_id = max(actual_ids)
                    if next_actual_id < from_id:
                        boundary_complete = False
                        _add_reason(reasons, "pagination_no_progress")
                        break
                    if all(value > slice_end for value in actual_times) or len(more_rows) < 1000:
                        slice_done = True
                    else:
                        from_id = next_actual_id + 1
                if not pages_complete or not boundary_complete:
                    break
            slice_start = slice_end + 1

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
        offset_ms = self._server_offset(context)
        payload = self._private_get(_ACCOUNT_PATH, {}, context, offset_ms)
        account = _expect_mapping(payload)
        context.advance(pages=1, records=0)
        raw_rows = account.get("balances")
        if not isinstance(raw_rows, list):
            raise DataAPIError("invalid_response")
        rejected = 0
        duplicates = 0
        reasons: list[str] = []
        balances_by_asset: dict[str, dict[str, str]] = {}
        for raw in raw_rows:
            try:
                if not isinstance(raw, Mapping):
                    raise ValueError("balance")
                asset = raw.get("asset")
                if not isinstance(asset, str) or not asset or len(asset) > 32:
                    raise ValueError("balance")
                balance = {
                    "asset": asset,
                    "free": _decimal_text(raw.get("free"), nonnegative=True),
                    "locked": _decimal_text(raw.get("locked"), nonnegative=True),
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

    def _server_offset(self, context: SyncContext) -> int:
        context.check()
        payload = self._transport.get_json(
            _PUBLIC_HOST,
            _TIME_PATH,
            cancel_event=context.cancel_event,
        )
        response = _expect_mapping(payload)
        try:
            server_ms = _strict_int(response.get("serverTime"), minimum=0)
        except ValueError:
            raise DataAPIError("invalid_response") from None
        local_ms = self._live_timestamp()
        offset = server_ms - local_ms
        if abs(offset) > 30_000:
            raise DataAPIError("invalid_response")
        context.advance(pages=1, records=0)
        return offset

    def _symbol_assets(self, symbol: str, context: SyncContext) -> tuple[str, str]:
        context.check()
        payload = self._transport.get_json(
            _PUBLIC_HOST,
            _EXCHANGE_INFO_PATH,
            {"symbol": symbol},
            cancel_event=context.cancel_event,
        )
        response = _expect_mapping(payload)
        symbols = response.get("symbols")
        if not isinstance(symbols, list):
            raise DataAPIError("invalid_response")
        matches = [row for row in symbols if isinstance(row, Mapping) and row.get("symbol") == symbol]
        if len(matches) != 1:
            raise DataAPIError("invalid_response")
        base_asset = matches[0].get("baseAsset")
        quote_asset = matches[0].get("quoteAsset")
        if not isinstance(base_asset, str) or not base_asset or len(base_asset) > 32:
            raise DataAPIError("invalid_response")
        if not isinstance(quote_asset, str) or not quote_asset or len(quote_asset) > 32:
            raise DataAPIError("invalid_response")
        context.advance(pages=1, records=len(matches))
        return base_asset, quote_asset

    def _private_get(
        self,
        path: str,
        params: Mapping[str, Any],
        context: SyncContext,
        offset_ms: int,
    ) -> Any:
        api_key, api_secret = _credential_values(self._credentials)
        items = list(params.items())
        items.append(("recvWindow", 5000))
        items.append(("timestamp", self._live_timestamp() + offset_ms))
        query_string = urllib.parse.urlencode(items, doseq=False, safe="")
        signature = hmac.new(
            api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_params = {key: value for key, value in items}
        signed_params["signature"] = signature
        return self._transport.get_json(
            _PRIVATE_HOST,
            path,
            signed_params,
            {"X-MBX-APIKEY": api_key},
            cancel_event=context.cancel_event,
        )

    def _live_timestamp(self) -> int:
        value = self._clock_ms()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DataAPIError("invalid_response")
        return value


__all__ = ("BinanceDataClient",)
