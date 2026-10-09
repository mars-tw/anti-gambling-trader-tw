"""Strict request validation and decimal canonicalization for API data."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

PROVIDERS = frozenset({"pionex", "binance", "shioaji"})
QUERY_KINDS = frozenset({"candles", "fills", "account"})
INTERVALS = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d"})
QUERY_KEYS = frozenset(
    {
        "symbol",
        "market",
        "interval",
        "start_ms",
        "end_ms",
        "limit",
        "account_ref",
    }
)

MAX_PAGES = 20
MAX_RETURNED_ROWS = 5000
MAX_DATASET_BYTES = 4 * 1024 * 1024
MAX_RANGE_MS = 31 * 24 * 60 * 60 * 1000
DEFAULT_RANGE_MS = 24 * 60 * 60 * 1000
FUTURE_SKEW_MS = 60 * 1000

_PIONEX_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}_[A-Z0-9]{1,20}$", re.ASCII)
_ALNUM_SYMBOL = re.compile(r"^[A-Za-z0-9]{1,32}$", re.ASCII)
_ACCOUNT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", re.ASCII)


def _fail(field: str) -> ValueError:
    return ValueError(f"invalid_{field}")


def _strict_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _fail(field)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise _fail(field)
    return value


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(field)
    return value


def normalize_decimal(value: Any, *, allow_negative: bool = False) -> str:
    """Return a finite canonical decimal string without binary-float math.

    Non-negative values are the safe default for prices, quantities, volume,
    and fees.  Signed fields such as realized P&L opt in explicitly.
    """

    if not isinstance(allow_negative, bool):
        raise ValueError("allow_negative")
    if isinstance(value, bool) or value is None:
        raise ValueError("decimal")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("decimal")
        number = Decimal(repr(value))
    elif isinstance(value, str):
        if (
            not value
            or value != value.strip()
            or len(value) > 128
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        ):
            raise ValueError("decimal")
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError("decimal") from None
    else:
        raise ValueError("decimal")
    if not number.is_finite():
        raise ValueError("decimal")
    if number < 0 and not allow_negative:
        raise ValueError("decimal")
    if number.is_zero():
        return "0"
    digits = number.as_tuple().digits
    adjusted = number.adjusted()
    if len(digits) > 1000 or adjusted < -1000 or adjusted > 1000:
        raise ValueError("decimal")
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text == "-0":
        return "0"
    return text


def _validate_symbol(provider: str, value: Any) -> str:
    symbol = _strict_string(value, "symbol")
    pattern = _PIONEX_SYMBOL if provider == "pionex" else _ALNUM_SYMBOL
    if pattern.fullmatch(symbol) is None:
        raise _fail("symbol")
    return symbol


def _validate_market(provider: str, value: Any) -> str:
    if value is None and provider in {"pionex", "binance"}:
        return "spot"
    market = _strict_string(value, "market")
    if provider in {"pionex", "binance"}:
        if market != "spot":
            raise _fail("market")
    elif market not in {"stock", "futures"}:
        raise _fail("market")
    return market


def _validate_account_ref(value: Any) -> str:
    account_ref = _strict_string(value, "account_ref")
    if _ACCOUNT_REF.fullmatch(account_ref) is None:
        raise _fail("account_ref")
    return account_ref


def validate_query(provider: str, kind: str, query: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one bounded, read-only provider query and apply safe defaults."""

    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise _fail("provider")
    if not isinstance(kind, str) or kind not in QUERY_KINDS:
        raise _fail("kind")
    if not isinstance(query, Mapping):
        raise _fail("query")
    if any(not isinstance(key, str) or key not in QUERY_KEYS for key in query):
        raise _fail("query_key")

    temporal = kind in {"candles", "fills"}
    allowed = {
        "candles": {"symbol", "market", "interval", "start_ms", "end_ms", "limit"},
        "fills": {"symbol", "market", "start_ms", "end_ms", "limit", "account_ref"},
        "account": {"symbol", "market", "account_ref"},
    }[kind]
    if any(key not in allowed for key in query):
        raise _fail("query_key")

    result: dict[str, Any] = {}
    result["market"] = _validate_market(provider, query.get("market"))

    if kind in {"candles", "fills"}:
        if "symbol" not in query:
            raise _fail("symbol")
        result["symbol"] = _validate_symbol(provider, query["symbol"])
    elif "symbol" in query:
        result["symbol"] = _validate_symbol(provider, query["symbol"])

    if kind == "candles":
        interval = _strict_string(query.get("interval"), "interval")
        if interval not in INTERVALS:
            raise _fail("interval")
        result["interval"] = interval

    if temporal:
        captured_now = time.time_ns() // 1_000_000
        end_ms = (
            _strict_int(query["end_ms"], "end_ms")
            if "end_ms" in query
            else captured_now
        )
        start_ms = (
            _strict_int(query["start_ms"], "start_ms")
            if "start_ms" in query
            else max(0, end_ms - DEFAULT_RANGE_MS)
        )
        if start_ms < 0 or end_ms < 0 or start_ms > end_ms:
            raise _fail("time_range")
        if end_ms > captured_now + FUTURE_SKEW_MS:
            raise _fail("end_ms")
        if end_ms - start_ms > MAX_RANGE_MS:
            raise _fail("time_range")
        result["start_ms"] = start_ms
        result["end_ms"] = end_ms

        max_limit = 2000 if kind == "candles" else 5000
        default_limit = 500 if kind == "candles" else 5000
        limit = (
            _strict_int(query["limit"], "limit")
            if "limit" in query
            else default_limit
        )
        if limit < 1 or limit > max_limit:
            raise _fail("limit")
        result["limit"] = limit

    if "account_ref" in query:
        result["account_ref"] = _validate_account_ref(query["account_ref"])

    return result
