"""Provider factory and small schema helpers for read-only API datasets.

The concrete clients are imported lazily.  Importing this module therefore
does not import Shioaji (or any other optional SDK), open a credential store,
or start a subprocess.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from core.data_api.transport import DataAPIError, ReadOnlyHTTPTransport

SCHEMA_VERSION = "api-data-v1"
MAX_PAGES = 20
MAX_ROWS = 5000
MAX_DATASET_BYTES = 4 * 1024 * 1024

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def create_client(
    provider: str,
    *,
    transport: ReadOnlyHTTPTransport | None = None,
    credentials: Any = None,
) -> Any:
    """Create one of the three bounded, read-only data clients."""

    if provider == "pionex":
        from core.data_api.pionex import PionexDataClient

        return PionexDataClient(transport=transport, credentials=credentials)
    if provider == "binance":
        from core.data_api.binance import BinanceDataClient

        return BinanceDataClient(transport=transport, credentials=credentials)
    if provider == "shioaji":
        from core.data_api.shioaji import ShioajiDataClient

        return ShioajiDataClient(credentials=credentials)
    raise ValueError("provider")


def _context_now_ms(context: Any) -> int:
    value = getattr(context, "now_ms", None)
    if callable(value):
        value = value()
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataAPIError("invalid_response")
    return value


def _utc_iso(epoch_ms: int) -> str:
    try:
        moment = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise DataAPIError("invalid_response") from None
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _requested(query: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "start_ms": query.get("start_ms"),
        "end_ms": query.get("end_ms"),
        "interval": query.get("interval"),
        "limit": query.get("limit"),
    }


def _coverage(
    *,
    complete: bool,
    pages_complete: bool,
    boundary_complete: bool,
    truncated: bool,
    rejected_rows: int,
    duplicate_rows: int,
    missing_bars: int,
    reasons: list[str],
    rows: list[Mapping[str, Any]],
    raw_count: int,
    time_key: str = "time_ms",
) -> dict[str, Any]:
    times = [row.get(time_key) for row in rows]
    valid_times = [
        value
        for value in times
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    return {
        "complete": bool(complete),
        "pages_complete": bool(pages_complete),
        "boundary_complete": bool(boundary_complete),
        "truncated": bool(truncated),
        "rejected_rows": int(rejected_rows),
        "duplicate_rows": int(duplicate_rows),
        "missing_bars": int(missing_bars),
        "reasons": list(dict.fromkeys(reasons)),
        "actual_start_ms": min(valid_times) if valid_times else None,
        "actual_end_ms": max(valid_times) if valid_times else None,
        "raw_count": int(raw_count),
    }


def _dataset(
    *,
    provider: str,
    kind: str,
    market: str,
    symbol: str,
    captured_ms: int,
    timezone_name: str,
    query: Mapping[str, Any],
    rows: list[dict[str, Any]],
    coverage: Mapping[str, Any],
    summary: Mapping[str, Any],
    data_type: str,
    authentication_verified: bool,
) -> dict[str, Any]:
    dataset = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "kind": kind,
        "market": market,
        "symbol": symbol,
        "captured_at": _utc_iso(captured_ms),
        "timezone": timezone_name,
        "requested": _requested(query),
        "rows": rows,
        "coverage": dict(coverage),
        "summary": dict(summary),
        "provenance": {
            "data_type": data_type,
            "source_scope": "selected-symbol",
            "authentication_verified": bool(authentication_verified),
        },
    }
    try:
        encoded = json.dumps(
            dataset,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise DataAPIError("invalid_response") from None
    if len(encoded) > MAX_DATASET_BYTES:
        raise DataAPIError("oversize")
    return dataset


def _decimal_text(
    value: Any,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> str:
    """Validate a decimal while preserving an API-provided string exactly."""

    if isinstance(value, bool) or value is None:
        raise ValueError("decimal")
    if isinstance(value, str):
        if not value or value != value.strip() or any(ord(ch) < 32 for ch in value):
            raise ValueError("decimal")
        text = value
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("decimal")
        text = str(value)
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("decimal")
        text = repr(value)
    else:
        raise ValueError("decimal")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("decimal") from None
    if not number.is_finite():
        raise ValueError("decimal")
    if positive and number <= 0:
        raise ValueError("decimal")
    if nonnegative and number < 0:
        raise ValueError("decimal")
    return text


def _calculated_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _strict_int(value: Any, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError("integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value and value == value.strip():
        if value[0] in "+-":
            digits = value[1:]
        else:
            digits = value
        if not digits.isdigit():
            raise ValueError("integer")
        result = int(value, 10)
    else:
        raise ValueError("integer")
    if minimum is not None and result < minimum:
        raise ValueError("integer")
    return result


def _id_text(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("id")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if not value or value != value.strip() or len(value) > 256:
            raise ValueError("id")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("id")
        return value
    raise ValueError("id")


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _missing_crypto_bars(rows: list[Mapping[str, Any]], interval_ms: int) -> int:
    if interval_ms <= 0:
        return 0
    times = sorted(
        {
            value
            for value in (row.get("time_ms") for row in rows)
            if isinstance(value, int) and not isinstance(value, bool)
        }
    )
    missing = 0
    for previous, current in zip(times, times[1:]):
        delta = current - previous
        if delta > interval_ms:
            missing += max(0, delta // interval_ms - 1)
    return missing


def _credential_values(credentials: Any) -> tuple[str, str]:
    if credentials is None:
        raise DataAPIError("auth")
    api_key = getattr(credentials, "api_key", None)
    api_secret = getattr(credentials, "api_secret", None)
    if not isinstance(api_key, str) or not isinstance(api_secret, str):
        raise DataAPIError("auth")
    if not api_key or not api_secret:
        raise DataAPIError("auth")
    if len(api_key) > 512 or len(api_secret) > 512:
        raise DataAPIError("auth")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in api_key + api_secret):
        raise DataAPIError("auth")
    return api_key, api_secret


__all__ = (
    "BinanceDataClient",
    "PionexDataClient",
    "ShioajiDataClient",
    "create_client",
)


def __getattr__(name: str) -> Any:
    """Lazy class exports without importing optional provider dependencies."""

    if name == "PionexDataClient":
        from core.data_api.pionex import PionexDataClient

        return PionexDataClient
    if name == "BinanceDataClient":
        from core.data_api.binance import BinanceDataClient

        return BinanceDataClient
    if name == "ShioajiDataClient":
        from core.data_api.shioaji import ShioajiDataClient

        return ShioajiDataClient
    raise AttributeError(name)
