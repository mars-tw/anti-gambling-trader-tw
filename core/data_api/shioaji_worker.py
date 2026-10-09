"""Isolated Shioaji read-only worker.

The optional SDK is imported only inside request handling.  Credentials are
accepted only in the anonymous stdin request frame and are never read from
arguments, environment variables, or files.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import logging
import os
import sys
import warnings
from collections.abc import Mapping
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python >= 3.10 is required by the package
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]

from core.data_api.providers import (
    INTERVAL_MS,
    MAX_DATASET_BYTES,
    MAX_PAGES,
    MAX_ROWS,
    _add_reason,
    _calculated_decimal,
    _coverage,
    _dataset,
    _decimal_text,
    _id_text,
    _strict_int,
)

PROTOCOL = "agt-data-worker-v1"
_MAX_REQUEST_BYTES = 128 * 1024
_SAFE_MESSAGES = {
    "auth": "認證失敗",
    "rate_limit": "請求過於頻繁",
    "timeout": "連線逾時",
    "network": "工作程序通訊通道不可用",
    "invalid_response": "回應格式無效",
    "oversize": "回應過大",
    "cancelled": "已取消",
}
_MISSING = object()
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_HISTORICAL_NS_TIMESTAMP_BASIS = "sdk_historical_ns_encoded_local_wall_time"

if ZoneInfo is not None:
    try:
        _TAIPEI = ZoneInfo("Asia/Taipei")
    except ZoneInfoNotFoundError:
        _TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")
else:
    _TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")


class _WorkerFailure(Exception):
    def __init__(self, code: str):
        self.code = code if code in _SAFE_MESSAGES else "invalid_response"
        super().__init__(_SAFE_MESSAGES[self.code])


def _ok(result: Any) -> dict[str, Any]:
    return {"protocol": PROTOCOL, "ok": True, "result": result}


def _error(code: str) -> dict[str, Any]:
    safe_code = code if code in _SAFE_MESSAGES else "invalid_response"
    return {
        "protocol": PROTOCOL,
        "ok": False,
        "error": {"code": safe_code, "message": _SAFE_MESSAGES[safe_code]},
    }


def _plain(value: Any) -> Any:
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            return value
    return value


def _obj_get(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return _plain(obj[name])
        try:
            value = getattr(obj, name)
        except (AttributeError, TypeError):
            continue
        except Exception:
            continue
        return _plain(value)
    return default


def _enum_text(value: Any) -> str:
    raw = _obj_get(value, "value", default=value)
    return str(raw)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise _WorkerFailure("invalid_response")
    try:
        return list(value)
    except (TypeError, ValueError):
        raise _WorkerFailure("invalid_response") from None


def _load_sdk() -> Any:
    # Shioaji reads its log path during import; force the isolated worker's
    # sink before the optional SDK can initialize any global logging state.
    os.environ["SJ_LOG_PATH"] = os.devnull
    try:
        return importlib.import_module("shioaji")
    except Exception:
        raise _WorkerFailure("invalid_response") from None


def _sdk_info(sdk_module: Any = None) -> dict[str, Any]:
    sdk = sdk_module if sdk_module is not None else _load_sdk()
    version = getattr(sdk, "__version__", None)
    if not isinstance(version, str) or not version:
        version = "unknown"
    return {
        "sdk": "shioaji",
        "version": version,
        "available": True,
        "login_performed": False,
        "read_only": True,
    }


def _validate_request(request: Any) -> tuple[str, dict[str, Any], int, str, str]:
    if not isinstance(request, Mapping) or request.get("protocol") != PROTOCOL:
        raise _WorkerFailure("invalid_response")
    if request.get("action") != "fetch":
        raise _WorkerFailure("invalid_response")
    capabilities = request.get("request_capabilities")
    if not isinstance(capabilities, Mapping):
        raise _WorkerFailure("invalid_response")
    if capabilities.get("read_only") is not True:
        raise _WorkerFailure("invalid_response")
    if capabilities.get("allow_orders") is not False:
        raise _WorkerFailure("invalid_response")
    if capabilities.get("allow_ca") is not False:
        raise _WorkerFailure("invalid_response")
    kind = request.get("kind")
    if kind not in {"candles", "fills", "realizations", "account"}:
        raise _WorkerFailure("invalid_response")
    query = request.get("query")
    if not isinstance(query, Mapping):
        raise _WorkerFailure("invalid_response")
    validated = dict(query)
    market = validated.get("market")
    if market not in {"stock", "futures"}:
        raise _WorkerFailure("invalid_response")
    symbol = validated.get("symbol", "")
    if not isinstance(symbol, str) or len(symbol) > 32:
        raise _WorkerFailure("invalid_response")
    if kind != "account" and not symbol:
        raise _WorkerFailure("invalid_response")
    try:
        start_ms = _strict_int(validated.get("start_ms"), minimum=0)
        end_ms = _strict_int(validated.get("end_ms"), minimum=0)
        limit = _strict_int(validated.get("limit"), minimum=1)
        captured_ms = _strict_int(request.get("captured_at_ms"), minimum=0)
    except ValueError:
        raise _WorkerFailure("invalid_response") from None
    if start_ms > end_ms or end_ms > captured_ms + 60_000:
        raise _WorkerFailure("invalid_response")
    if limit > (2000 if kind == "candles" else MAX_ROWS):
        raise _WorkerFailure("invalid_response")
    validated["start_ms"] = start_ms
    validated["end_ms"] = end_ms
    validated["limit"] = limit
    if kind == "candles":
        interval = validated.get("interval")
        if interval not in INTERVAL_MS:
            raise _WorkerFailure("invalid_response")
    account_ref = validated.get("account_ref")
    if account_ref is not None and (
        not isinstance(account_ref, str) or not account_ref or len(account_ref) > 80
    ):
        raise _WorkerFailure("invalid_response")
    credentials = request.get("credentials")
    if not isinstance(credentials, Mapping):
        raise _WorkerFailure("auth")
    api_key = credentials.get("api_key")
    api_secret = credentials.get("api_secret")
    if not isinstance(api_key, str) or not isinstance(api_secret, str):
        raise _WorkerFailure("auth")
    if not api_key or not api_secret or len(api_key) > 512 or len(api_secret) > 512:
        raise _WorkerFailure("auth")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in api_key + api_secret):
        raise _WorkerFailure("auth")
    return str(kind), validated, captured_ms, api_key, api_secret


def _account_market(account: Any, api: Any) -> str | None:
    stock_primary = _obj_get(api, "stock_account")
    futures_primary = _obj_get(api, "futopt_account")
    if account is stock_primary and account is not None:
        return "stock"
    if account is futures_primary and account is not None:
        return "futures"
    account_type = _enum_text(_obj_get(account, "account_type", "type", default="")).lower()
    class_name = type(account).__name__.lower()
    combined = f"{account_type} {class_name}"
    if "stock" in combined or "securities" in combined:
        return "stock"
    if "future" in combined or "futopt" in combined or "option" in combined:
        return "futures"
    return None


def _raw_account_identity(account: Any, market: str, index: int) -> str:
    parts = [market]
    for name_group in (
        ("broker_id", "brokerId"),
        ("account_id", "accountId", "id"),
        ("person_id", "personId"),
    ):
        value = _obj_get(account, *name_group)
        if value is not None:
            parts.append(str(value))
    if len(parts) == 1:
        parts.extend((type(account).__name__, str(index)))
    return "\x1f".join(parts)


def _masked_label(account: Any, market: str) -> str:
    value = _obj_get(account, "account_id", "accountId", "id", default="")
    text = str(value) if value is not None else ""
    tail = text[-4:] if len(text) >= 4 else ""
    prefix = "證券" if market == "stock" else "期貨"
    return f"{prefix} ••••{tail}" if tail else f"{prefix} ••••"


def _collect_accounts(
    api: Any,
    login_result: Any,
    market: str,
    api_key: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    candidates: list[Any] = []
    if isinstance(login_result, (list, tuple)):
        candidates.extend(login_result)
    nested = _obj_get(login_result, "accounts")
    if isinstance(nested, (list, tuple)):
        candidates.extend(nested)
    api_accounts = _obj_get(api, "accounts")
    if isinstance(api_accounts, (list, tuple)):
        candidates.extend(api_accounts)
    for property_name in ("stock_account", "futopt_account"):
        account = _obj_get(api, property_name)
        if account is not None:
            candidates.append(account)

    descriptors: list[dict[str, str]] = []
    by_ref: dict[str, Any] = {}
    seen_objects: set[int] = set()
    for index, account in enumerate(candidates):
        object_id = id(account)
        if object_id in seen_objects:
            continue
        seen_objects.add(object_id)
        account_market = _account_market(account, api)
        if account_market != market:
            continue
        identity = _raw_account_identity(account, account_market, index)
        digest = hmac.new(
            api_key.encode("utf-8"),
            identity.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        ref = f"acct_{digest}"
        if ref in by_ref:
            continue
        by_ref[ref] = account
        descriptors.append(
            {
                "ref": ref,
                "label": _masked_label(account, account_market),
                "market": account_market,
            }
        )
    return descriptors, by_ref


def _select_account(
    query: Mapping[str, Any],
    descriptors: list[dict[str, str]],
    by_ref: Mapping[str, Any],
) -> tuple[Any | None, bool]:
    requested = query.get("account_ref")
    if requested is not None:
        selected = by_ref.get(requested)
        if selected is None:
            raise _WorkerFailure("invalid_response")
        return selected, False
    if len(descriptors) == 1:
        return by_ref[descriptors[0]["ref"]], False
    if len(descriptors) > 1:
        return None, True
    raise _WorkerFailure("invalid_response")


def _unit_for(sdk: Any, market: str) -> Any:
    unit_type = getattr(sdk, "Unit", None)
    if unit_type is None:
        raise _WorkerFailure("invalid_response")
    name = "Share" if market == "stock" else "Common"
    try:
        return getattr(unit_type, name)
    except AttributeError:
        raise _WorkerFailure("invalid_response") from None


def _lookup_collection(collection: Any, key: str, *, allow_uppercase: bool) -> Any:
    getter = getattr(collection, "get", None)
    if callable(getter):
        try:
            found = getter(key)
        except Exception:
            found = None
        if found is not None:
            return found
    if isinstance(collection, Mapping) and key in collection:
        return collection[key]
    contains = getattr(collection, "__contains__", None)
    getitem = getattr(collection, "__getitem__", None)
    if callable(contains) and callable(getitem):
        try:
            if contains(key):
                return getitem(key)
        except Exception:
            return None
    upper = key.upper()
    if allow_uppercase and upper != key and callable(contains) and callable(getitem):
        try:
            if contains(upper):
                return getitem(upper)
        except Exception:
            return None
    return None


def _contract(api: Any, symbol: str, market: str) -> Any:
    current = _obj_get(api, "contracts")
    if current is not None:
        found = _lookup_collection(current, symbol, allow_uppercase=False)
        if found is not None:
            if isinstance(found, (list, tuple)):
                if len(found) == 1:
                    return found[0]
            else:
                return found
    legacy = _obj_get(api, "Contracts")
    if legacy is not None:
        direct = _lookup_collection(legacy, symbol, allow_uppercase=False)
        if direct is not None:
            return direct[0] if isinstance(direct, (list, tuple)) and len(direct) == 1 else direct
        group_name = "Stocks" if market == "stock" else "Futures"
        group = _obj_get(legacy, group_name)
        if group is not None:
            found = _lookup_collection(group, symbol, allow_uppercase=True)
            if found is not None:
                return found[0] if isinstance(found, (list, tuple)) and len(found) == 1 else found
    raise _WorkerFailure("invalid_response")


def _local_date(epoch_ms: int) -> str:
    try:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=_TAIPEI).date().isoformat()
    except (OSError, OverflowError, ValueError):
        raise _WorkerFailure("invalid_response") from None


def _date_value(value: Any) -> tuple[str, int]:
    value = _plain(value)
    if isinstance(value, datetime):
        local = value.astimezone(_TAIPEI) if value.tzinfo else value.replace(tzinfo=_TAIPEI)
        day = local.date()
    elif isinstance(value, date):
        day = value
    elif isinstance(value, str):
        text = value.strip()
        if not text or text != value:
            raise ValueError("date")
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError("date") from None
    else:
        raise ValueError("date")
    moment = datetime.combine(day, datetime_time.min, tzinfo=_TAIPEI)
    return day.isoformat(), int(moment.timestamp() * 1000)


def _datetime_to_epoch_ms(value: datetime) -> int:
    try:
        aware = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=_TAIPEI)
        utc_value = aware.astimezone(timezone.utc)
        delta = utc_value - _UTC_EPOCH
    except (OSError, OverflowError, TypeError, ValueError):
        raise ValueError("timestamp") from None
    return (
        delta.days * 86_400_000
        + delta.seconds * 1000
        + delta.microseconds // 1000
    )


def _iso_timestamp_ms(value: str) -> int:
    text = value.strip()
    if not text or text != value or ("T" not in text and " " not in text):
        raise ValueError("timestamp")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("timestamp") from None
    return _datetime_to_epoch_ms(parsed)


def _historical_ns_to_epoch_ms(encoded_ns: int) -> int:
    """Decode Shioaji's historical nanosecond local-wall-time representation."""

    try:
        encoded_wall_utc = _UTC_EPOCH + timedelta(milliseconds=encoded_ns // 1_000_000)
    except (OverflowError, ValueError):
        raise ValueError("timestamp") from None
    return _datetime_to_epoch_ms(encoded_wall_utc.replace(tzinfo=None))


def _timestamp_ms(value: Any) -> int:
    value = _plain(value)
    if isinstance(value, datetime):
        return _datetime_to_epoch_ms(value)
    if isinstance(value, str) and ("T" in value or " " in value):
        return _iso_timestamp_ms(value)
    integer = _strict_int(value, minimum=0)
    if integer >= 100_000_000_000_000:
        return _historical_ns_to_epoch_ms(integer)
    if integer >= 100_000_000_000:
        return integer
    if integer >= 1_000_000_000:
        return integer * 1000
    raise ValueError("timestamp")


def _column_values(value: Any) -> list[Any]:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ValueError("column")
    try:
        length = len(value)
    except (TypeError, ValueError):
        raise ValueError("column") from None
    return [_plain(value[index]) for index in range(length)]


def _kbar_records(payload: Any) -> tuple[list[dict[str, Any]], int]:
    if isinstance(payload, Mapping):
        aliases = {
            "time": ("ts", "time", "timestamp"),
            "open": ("Open", "open"),
            "high": ("High", "high"),
            "low": ("Low", "low"),
            "close": ("Close", "close"),
            "volume": ("Volume", "volume"),
        }
        columns: dict[str, list[Any]] = {}
        for target, names in aliases.items():
            source = None
            for name in names:
                if name in payload:
                    source = payload[name]
                    break
            if source is None:
                raise _WorkerFailure("invalid_response")
            try:
                columns[target] = _column_values(source)
            except ValueError:
                raise _WorkerFailure("invalid_response") from None
        lengths = [len(column) for column in columns.values()]
        count = min(lengths, default=0)
        rejected = max(lengths, default=0) - count
        return (
            [
                {name: values[index] for name, values in columns.items()}
                for index in range(count)
            ],
            rejected,
        )
    records = _as_list(payload)
    normalized: list[dict[str, Any]] = []
    for item in records:
        normalized.append(
            {
                "time": _obj_get(item, "ts", "time", "timestamp"),
                "open": _obj_get(item, "Open", "open"),
                "high": _obj_get(item, "High", "high"),
                "low": _obj_get(item, "Low", "low"),
                "close": _obj_get(item, "Close", "close"),
                "volume": _obj_get(item, "Volume", "volume"),
            }
        )
    return normalized, 0


def _parse_kbar(raw: Mapping[str, Any]) -> dict[str, Any]:
    time_ms = _timestamp_ms(raw.get("time"))
    open_text = _decimal_text(_plain(raw.get("open")), positive=True)
    high_text = _decimal_text(_plain(raw.get("high")), positive=True)
    low_text = _decimal_text(_plain(raw.get("low")), positive=True)
    close_text = _decimal_text(_plain(raw.get("close")), positive=True)
    volume_text = _decimal_text(_plain(raw.get("volume")), nonnegative=True)
    if Decimal(high_text) < max(Decimal(open_text), Decimal(low_text), Decimal(close_text)):
        raise ValueError("kbar")
    if Decimal(low_text) > min(Decimal(open_text), Decimal(high_text), Decimal(close_text)):
        raise ValueError("kbar")
    return {
        "time_ms": time_ms,
        "open": open_text,
        "high": high_text,
        "low": low_text,
        "close": close_text,
        "volume": volume_text,
    }


def _bucket_start(epoch_ms: int, interval: str) -> int:
    local = datetime.fromtimestamp(epoch_ms / 1000, tz=_TAIPEI)
    if interval == "1d":
        bucket = datetime.combine(local.date(), datetime_time.min, tzinfo=_TAIPEI)
    else:
        minutes = INTERVAL_MS[interval] // 60_000
        minute_of_day = local.hour * 60 + local.minute
        bucket_minute = minute_of_day - minute_of_day % minutes
        bucket = datetime.combine(local.date(), datetime_time.min, tzinfo=_TAIPEI) + timedelta(
            minutes=bucket_minute
        )
    return int(bucket.timestamp() * 1000)


def _aggregate_bars(
    raw_rows: list[dict[str, Any]],
    interval: str,
    captured_ms: int,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in sorted(raw_rows, key=lambda item: item["time_ms"]):
        bucket = _bucket_start(row["time_ms"], interval)
        grouped.setdefault(bucket, []).append(row)
    result: list[dict[str, Any]] = []
    interval_ms = INTERVAL_MS[interval]
    for bucket in sorted(grouped):
        members = grouped[bucket]
        high_row = max(members, key=lambda item: Decimal(item["high"]))
        low_row = min(members, key=lambda item: Decimal(item["low"]))
        volume = sum((Decimal(item["volume"]) for item in members), Decimal("0"))
        result.append(
            {
                "time_ms": bucket,
                "open": members[0]["open"],
                "high": high_row["high"],
                "low": low_row["low"],
                "close": members[-1]["close"],
                "volume": _calculated_decimal(volume),
                "is_closed": bucket + interval_ms <= captured_ms,
            }
        )
    return result


def _aggregate_bars_with_source_coverage(
    raw_rows: list[dict[str, Any]],
    interval: str,
    captured_ms: int,
    query_start_ms: int,
    query_end_ms: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Aggregate only query-bounded buckets with provable minute coverage."""
    if interval == "1m":
        missing = _stock_missing_bars(raw_rows, 60_000)
        return _aggregate_bars(raw_rows, interval, captured_ms), missing, (
            ["missing_source_minutes"] if missing else []
        )

    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in sorted(raw_rows, key=lambda item: item["time_ms"]):
        bucket = _bucket_start(row["time_ms"], interval)
        grouped.setdefault(bucket, []).append(row)
    result: list[dict[str, Any]] = []
    missing_source_minutes = 0
    reasons: list[str] = []
    source_minutes = INTERVAL_MS[interval] // 60_000
    for bucket, members in sorted(grouped.items()):
        last_source_minute = bucket + (source_minutes - 1) * 60_000
        if bucket < query_start_ms or last_source_minute > query_end_ms:
            _add_reason(reasons, "derived_bucket_outside_query")
            continue
        if interval == "1d":
            _add_reason(reasons, "daily_source_coverage_unproven")
        expected = {
            bucket + offset * 60_000 for offset in range(source_minutes)
        }
        actual = {row["time_ms"] for row in members}
        missing = len(expected - actual)
        if missing:
            missing_source_minutes += missing
            _add_reason(reasons, "missing_source_minutes")
            continue
        result.extend(_aggregate_bars(members, interval, captured_ms))
    if missing_source_minutes and "missing_source_minutes" not in reasons:
        _add_reason(reasons, "missing_source_minutes")
    return result, missing_source_minutes, reasons


def _stock_missing_bars(rows: list[Mapping[str, Any]], interval_ms: int) -> int:
    missing = 0
    ordered = sorted(row["time_ms"] for row in rows)
    for previous, current in zip(ordered, ordered[1:]):
        previous_day = datetime.fromtimestamp(previous / 1000, tz=_TAIPEI).date()
        current_day = datetime.fromtimestamp(current / 1000, tz=_TAIPEI).date()
        if previous_day == current_day and current - previous > interval_ms:
            missing += max(0, (current - previous) // interval_ms - 1)
    return missing


def _with_kbar_timestamp_basis(dataset: dict[str, Any]) -> dict[str, Any]:
    provenance = dataset.get("provenance")
    if not isinstance(provenance, dict):
        raise _WorkerFailure("invalid_response")
    provenance["platform_timestamp_basis"] = _HISTORICAL_NS_TIMESTAMP_BASIS
    try:
        encoded = json.dumps(
            dataset,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise _WorkerFailure("invalid_response") from None
    if len(encoded) > MAX_DATASET_BYTES:
        raise _WorkerFailure("oversize")
    return dataset


def _fetch_candles(
    api: Any,
    query: Mapping[str, Any],
    captured_ms: int,
) -> dict[str, Any]:
    symbol = query["symbol"]
    market = query["market"]
    interval = query["interval"]
    contract = _contract(api, symbol, market)
    try:
        payload = api.kbars(
            contract,
            start=_local_date(query["start_ms"]),
            end=_local_date(query["end_ms"]),
            timeout=5000,
        )
    except Exception:
        raise _WorkerFailure("invalid_response") from None
    records, column_rejected = _kbar_records(payload)
    raw_count = len(records) + column_rejected
    rejected = column_rejected
    duplicates = 0
    reasons: list[str] = []
    by_time: dict[int, dict[str, Any]] = {}
    for raw in records:
        try:
            parsed = _parse_kbar(raw)
            if parsed["time_ms"] < query["start_ms"] or parsed["time_ms"] > query["end_ms"]:
                continue
            previous = by_time.get(parsed["time_ms"])
            if previous is None:
                by_time[parsed["time_ms"]] = parsed
            elif previous == parsed:
                duplicates += 1
            else:
                duplicates += 1
                rejected += 1
                _add_reason(reasons, "conflicting_timestamp")
        except (KeyError, TypeError, ValueError, InvalidOperation):
            rejected += 1
            _add_reason(reasons, "rejected_rows")
    if column_rejected:
        _add_reason(reasons, "rejected_rows")
    rows, missing, source_reasons = _aggregate_bars_with_source_coverage(
        list(by_time.values()),
        interval,
        captured_ms,
        query["start_ms"],
        query["end_ms"],
    )
    for reason in source_reasons:
        _add_reason(reasons, reason)
    truncated = False
    if len(rows) > query["limit"]:
        rows = rows[-query["limit"] :]
        truncated = True
        _add_reason(reasons, "row_limit")
    if missing:
        _add_reason(reasons, "missing_bars")
    if duplicates:
        _add_reason(reasons, "duplicate_rows")
    complete = (
        not truncated
        and rejected == 0
        and duplicates == 0
        and missing == 0
        and not source_reasons
    )
    coverage = _coverage(
        complete=complete,
        pages_complete=True,
        boundary_complete=not source_reasons,
        truncated=truncated,
        rejected_rows=rejected,
        duplicate_rows=duplicates,
        missing_bars=missing,
        reasons=reasons,
        rows=rows,
        raw_count=raw_count,
    )
    dataset = _dataset(
        provider="shioaji",
        kind="candles",
        market=market,
        symbol=symbol,
        captured_ms=captured_ms,
        timezone_name="Asia/Taipei",
        query=query,
        rows=rows,
        coverage=coverage,
        summary={
            "row_count": len(rows),
            "latest_bar_time_ms": rows[-1]["time_ms"] if rows else None,
        },
        data_type="private-sdk-market-data",
        authentication_verified=True,
    )
    return _with_kbar_timestamp_basis(dataset)


def _optional_decimal(
    obj: Any,
    names: tuple[str, ...],
    *,
    nonnegative: bool = False,
) -> str | None:
    value = _obj_get(obj, *names)
    if value is None:
        return None
    return _decimal_text(_plain(value), nonnegative=nonnegative)


def _currency_code(value: Any) -> str:
    raw = _obj_get(value, "value", default=value)
    if isinstance(raw, bool) or not isinstance(raw, str) or raw != raw.strip():
        raise ValueError("currency")
    code = raw.upper()
    if len(code) != 3 or not code.isascii() or not code.isalpha():
        raise ValueError("currency")
    return code


def _explicit_currency_fields(item: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("pnl_currency", "currency"):
        value = _obj_get(item, name, default=_MISSING)
        if value is _MISSING or value is None:
            continue
        fields[name] = _currency_code(value)
    return fields


def _currency_values(item: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for name in ("pnl_currency", "currency"):
        value = item.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError("currency")
        values.add(_currency_code(value))
    return values


def _normalize_detail(item: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    raw_date = _obj_get(item, "date", "trade_date")
    if raw_date is not None:
        detail["date"] = _date_value(raw_date)[0]
    fields = (
        ("quantity", ("quantity", "qty"), False),
        ("price", ("price", "entry_price", "cover_price"), True),
        ("pnl", ("pnl", "profit_loss"), False),
        ("fee", ("fee", "handling_fee", "commission"), True),
        ("tax", ("tax", "trade_tax"), True),
        ("interest", ("interest", "financing_interest"), True),
        ("shortselling_fee", ("shortselling_fee", "short_selling_fee"), True),
    )
    for target, names, nonnegative in fields:
        value = _optional_decimal(item, names, nonnegative=nonnegative)
        if value is not None:
            detail[target] = value
    side = _obj_get(item, "side", "action")
    if side is not None:
        side_text = _enum_text(side).upper()
        if side_text in {"BUY", "SELL"}:
            detail["side"] = side_text
    currency_fields = _explicit_currency_fields(item)
    detail.update(currency_fields)
    values = set(currency_fields.values())
    if len(values) == 1:
        detail["currency"] = values.pop()
    return detail


def _sum_details(details: list[dict[str, Any]], field: str) -> str | None:
    if not details or any(field not in detail for detail in details):
        return None
    try:
        total = sum((Decimal(detail[field]) for detail in details), Decimal("0"))
    except (InvalidOperation, ValueError):
        return None
    return _calculated_decimal(total)


def _sum_observed_details(details: list[dict[str, Any]], field: str) -> str | None:
    """Sum only explicitly observed components; missing siblings stay unknown."""
    values = [detail[field] for detail in details if field in detail]
    if not values:
        return None
    try:
        total = sum((Decimal(value) for value in values), Decimal("0"))
    except (InvalidOperation, ValueError):
        return None
    return _calculated_decimal(total)


def _attach_realization_currency(
    row: dict[str, Any],
    summary_fields: Mapping[str, str],
    details: list[dict[str, Any]],
    reasons: list[str],
) -> bool:
    """Attach only explicit, mutually consistent settlement-currency evidence."""

    summary_values = set(summary_fields.values())
    if len(summary_values) > 1:
        row.pop("currency", None)
        _add_reason(reasons, "currency_conflict")
        return False

    detail_values: set[str] = set()
    detail_missing = False
    for detail in details:
        values = _currency_values(detail)
        if not values:
            detail_missing = True
            continue
        detail_values.update(values)

    all_values = summary_values | detail_values
    if len(all_values) > 1:
        row.pop("currency", None)
        _add_reason(reasons, "currency_conflict")
        return False

    if summary_values:
        row["currency"] = next(iter(summary_values))
        return True

    if not details or detail_missing or not detail_values:
        row.pop("currency", None)
        _add_reason(reasons, "currency_missing")
        return False

    row["currency"] = next(iter(detail_values))
    return True


def _selection_required_dataset(
    *,
    kind: str,
    query: Mapping[str, Any],
    captured_ms: int,
    descriptors: list[dict[str, str]],
) -> dict[str, Any]:
    reasons = ["account_selection_required"]
    coverage = _coverage(
        complete=False,
        pages_complete=True,
        boundary_complete=False,
        truncated=False,
        rejected_rows=0,
        duplicate_rows=0,
        missing_bars=0,
        reasons=reasons,
        rows=[],
        raw_count=0,
    )
    return _dataset(
        provider="shioaji",
        kind=kind,
        market=query["market"],
        symbol=query.get("symbol", ""),
        captured_ms=captured_ms,
        timezone_name="Asia/Taipei",
        query=query,
        rows=[],
        coverage=coverage,
        summary={"accounts": descriptors},
        data_type="private-account-data",
        authentication_verified=True,
    )


def _fetch_realizations(
    api: Any,
    sdk: Any,
    query: Mapping[str, Any],
    captured_ms: int,
    account: Any | None,
    descriptors: list[dict[str, str]],
    selection_required: bool,
) -> dict[str, Any]:
    if selection_required:
        return _selection_required_dataset(
            kind="realizations",
            query=query,
            captured_ms=captured_ms,
            descriptors=descriptors,
        )
    if account is None:
        raise _WorkerFailure("invalid_response")
    unit = _unit_for(sdk, query["market"])
    try:
        raw_rows = _as_list(
            api.list_profit_loss(
                account=account,
                begin_date=_local_date(query["start_ms"]),
                end_date=_local_date(query["end_ms"]),
                unit=unit,
                timeout=5000,
            )
        )
    except _WorkerFailure:
        raise
    except Exception:
        raise _WorkerFailure("invalid_response") from None

    rejected = 0
    duplicates = 0
    detail_calls = 0
    detail_raw_count = 0
    reasons: list[str] = []
    currency_complete = True
    rows_by_key: dict[str, dict[str, Any]] = {}
    date_ms_values: list[int] = []
    start_day = datetime.fromtimestamp(query["start_ms"] / 1000, tz=_TAIPEI).date()
    end_day = datetime.fromtimestamp(query["end_ms"] / 1000, tz=_TAIPEI).date()

    for raw in raw_rows:
        try:
            symbol = _obj_get(raw, "code", "symbol")
            if not isinstance(symbol, str) or not symbol or len(symbol) > 32:
                raise ValueError("symbol")
            if symbol != query["symbol"]:
                continue
            date_text, date_ms = _date_value(_obj_get(raw, "date", "trade_date"))
            row_day = date.fromisoformat(date_text)
            if row_day < start_day or row_day > end_day:
                continue
            quantity = _decimal_text(_obj_get(raw, "quantity", "qty"))
            pnl = _decimal_text(_obj_get(raw, "pnl", "profit_loss"))
            summary_currency = _explicit_currency_fields(raw)
            raw_detail_id = _obj_get(raw, "id", "detail_id")
            detail_id = _id_text(raw_detail_id) if raw_detail_id is not None else None
            details: list[dict[str, Any]] = []
            if raw_detail_id is not None:
                if 1 + detail_calls >= MAX_PAGES:
                    _add_reason(reasons, "detail_limit")
                else:
                    detail_calls += 1
                    try:
                        raw_details = _as_list(
                            api.list_profit_loss_detail(
                                account=account,
                                detail_id=raw_detail_id,
                                unit=unit,
                                timeout=5000,
                            )
                        )
                        detail_raw_count += len(raw_details)
                        for item in raw_details:
                            details.append(_normalize_detail(item))
                    except Exception:
                        _add_reason(reasons, "detail_error")
            # Only source summary fields are authoritative totals.  Detail
            # components remain evidence and must never be promoted into a
            # complete summary when the source omitted its totals.
            fee = _optional_decimal(raw, ("fee", "handling_fee", "commission"), nonnegative=True)
            tax = _optional_decimal(raw, ("tax", "trade_tax"), nonnegative=True)
            interest = _optional_decimal(
                raw, ("interest", "financing_interest"), nonnegative=True
            )
            shortselling_fee = _optional_decimal(
                raw, ("shortselling_fee", "short_selling_fee"), nonnegative=True
            )
            observed_detail_costs: dict[str, str] = {}
            for cost_name in ("fee", "tax", "interest", "shortselling_fee"):
                observed = _sum_observed_details(details, cost_name)
                if observed is not None:
                    observed_detail_costs[cost_name] = observed
            row: dict[str, Any] = {
                "symbol": symbol,
                "market": query["market"],
                "unit": "Share" if query["market"] == "stock" else "Common",
                "date": date_text,
                "time_precision": "date",
                "quantity": quantity,
                "pnl": pnl,
                "details": details,
            }
            for name, value in (
                ("fee", fee),
                ("tax", tax),
                ("interest", interest),
                ("shortselling_fee", shortselling_fee),
            ):
                if value is not None:
                    row[name] = value
            if observed_detail_costs:
                row["observed_detail_costs"] = observed_detail_costs
                row["cost_scope"] = (
                    "source_reported_totals_requires_confirmation"
                    if any(value is not None for value in (fee, tax, interest, shortselling_fee))
                    else "detail_components_unknown_total"
                )
            elif any(value is not None for value in (fee, tax, interest, shortselling_fee)):
                row["cost_scope"] = "source_reported_totals_requires_confirmation"
            row.update(summary_currency)
            if not _attach_realization_currency(row, summary_currency, details, reasons):
                currency_complete = False
            entry_price = _optional_decimal(raw, ("entry_price", "cost_price"), nonnegative=True)
            exit_price = _optional_decimal(raw, ("exit_price", "cover_price"), nonnegative=True)
            if entry_price is not None:
                row["entry_price"] = entry_price
            if exit_price is not None:
                row["exit_price"] = exit_price
            if detail_id is not None:
                row["detail_id"] = detail_id
            key = detail_id or "|".join((symbol, date_text, quantity, pnl))
            previous = rows_by_key.get(key)
            if previous is None:
                rows_by_key[key] = row
                date_ms_values.append(date_ms)
            elif previous == row:
                duplicates += 1
            else:
                duplicates += 1
                rejected += 1
                _add_reason(reasons, "conflicting_realization_id")
        except (KeyError, TypeError, ValueError, InvalidOperation):
            rejected += 1
            _add_reason(reasons, "rejected_rows")

    rows = sorted(rows_by_key.values(), key=lambda row: (row["date"], row.get("detail_id", "")))
    truncated = False
    if len(rows) > query["limit"]:
        rows = rows[-query["limit"] :]
        truncated = True
        _add_reason(reasons, "row_limit")
    if duplicates:
        _add_reason(reasons, "duplicate_rows")
    details_complete = "detail_limit" not in reasons and "detail_error" not in reasons
    complete = (
        not truncated
        and rejected == 0
        and duplicates == 0
        and details_complete
        and currency_complete
    )
    coverage = _coverage(
        complete=complete,
        pages_complete=details_complete,
        boundary_complete=details_complete,
        truncated=truncated,
        rejected_rows=rejected,
        duplicate_rows=duplicates,
        missing_bars=0,
        reasons=reasons,
        rows=[],
        raw_count=len(raw_rows) + detail_raw_count,
    )
    retained_days = [_date_value(row["date"])[1] for row in rows]
    coverage["actual_start_ms"] = min(retained_days) if retained_days else None
    coverage["actual_end_ms"] = max(retained_days) if retained_days else None
    return _dataset(
        provider="shioaji",
        kind="realizations",
        market=query["market"],
        symbol=query["symbol"],
        captured_ms=captured_ms,
        timezone_name="Asia/Taipei",
        query=query,
        rows=rows,
        coverage=coverage,
        summary={
            "row_count": len(rows),
            "accounts": descriptors,
            "intraday_time_known": False,
            "execution_order_known": False,
            "pnl_basis": "unknown",
        },
        data_type="private-realized-profit-loss",
        authentication_verified=True,
    )


def _fetch_account(
    api: Any,
    sdk: Any,
    query: Mapping[str, Any],
    captured_ms: int,
    account: Any | None,
    descriptors: list[dict[str, str]],
    selection_required: bool,
) -> dict[str, Any]:
    if selection_required:
        return _selection_required_dataset(
            kind="account",
            query=query,
            captured_ms=captured_ms,
            descriptors=descriptors,
        )
    if account is None:
        raise _WorkerFailure("invalid_response")
    unit = _unit_for(sdk, query["market"])
    try:
        raw_positions = _as_list(api.list_positions(account=account, unit=unit, timeout=5000))
        balance = api.account_balance(account=account, timeout=5000)
    except _WorkerFailure:
        raise
    except Exception:
        raise _WorkerFailure("invalid_response") from None
    rejected = 0
    duplicates = 0
    reasons: list[str] = []
    positions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_positions:
        try:
            symbol = _obj_get(raw, "code", "symbol")
            if not isinstance(symbol, str) or not symbol or len(symbol) > 32:
                raise ValueError("position")
            if query.get("symbol") and symbol != query["symbol"]:
                continue
            quantity = _decimal_text(_obj_get(raw, "quantity", "qty"))
            side_value = _obj_get(raw, "direction", "side")
            side = _enum_text(side_value).upper() if side_value is not None else ""
            key = (symbol, quantity, side)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            position: dict[str, Any] = {
                "symbol": symbol,
                "quantity": quantity,
                "unit": "Share" if query["market"] == "stock" else "Common",
                "cost_known": False,
            }
            if side in {"BUY", "SELL", "LONG", "SHORT"}:
                position["side"] = side
            positions.append(position)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            rejected += 1
            _add_reason(reasons, "rejected_rows")
    positions.sort(key=lambda row: (row["symbol"], row.get("side", "")))
    summary: dict[str, Any] = {"accounts": descriptors, "positions": positions}
    cash = _optional_decimal(balance, ("acc_balance", "cash_balance"))
    if cash is not None:
        summary["cash_balance"] = cash
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
        raw_count=len(raw_positions),
    )
    return _dataset(
        provider="shioaji",
        kind="account",
        market=query["market"],
        symbol=query.get("symbol", ""),
        captured_ms=captured_ms,
        timezone_name="Asia/Taipei",
        query=query,
        rows=[],
        coverage=coverage,
        summary=summary,
        data_type="private-account-data",
        authentication_verified=True,
    )


def handle_request(
    request: Any,
    *,
    sdk_module: Any = None,
) -> dict[str, Any]:
    """Handle one in-memory request; fake SDKs can be injected by tests."""

    api = None
    logged_in = False
    try:
        kind, query, captured_ms, api_key, api_secret = _validate_request(request)
        sdk = sdk_module if sdk_module is not None else _load_sdk()
        factory = getattr(sdk, "Shioaji", None)
        if not callable(factory):
            raise _WorkerFailure("invalid_response")
        try:
            api = factory()
            login_result = api.login(
                api_key=api_key,
                secret_key=api_secret,
                subscribe_trade=False,
                receive_window=30000,
                force_refresh=False,
            )
            logged_in = True
        except Exception:
            raise _WorkerFailure("auth") from None
        descriptors, by_ref = _collect_accounts(api, login_result, query["market"], api_key)
        if kind == "candles":
            result = _fetch_candles(api, query, captured_ms)
        else:
            account, selection_required = _select_account(query, descriptors, by_ref)
            if kind in {"fills", "realizations"}:
                result = _fetch_realizations(
                    api,
                    sdk,
                    query,
                    captured_ms,
                    account,
                    descriptors,
                    selection_required,
                )
            else:
                result = _fetch_account(
                    api,
                    sdk,
                    query,
                    captured_ms,
                    account,
                    descriptors,
                    selection_required,
                )
        return _ok(result)
    except _WorkerFailure as exc:
        return _error(exc.code)
    except Exception:
        return _error("invalid_response")
    finally:
        if logged_in and api is not None:
            try:
                api.logout()
            except Exception:
                logged_in = False


def _encode_frame(frame: Mapping[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            frame,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        payload = json.dumps(_error("invalid_response"), ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_DATASET_BYTES + 4096:
        payload = json.dumps(_error("oversize"), ensure_ascii=False).encode("utf-8")
    return payload + b"\n"


_WIN_STD_INPUT_HANDLE = -10
_WIN_STD_OUTPUT_HANDLE = -11
_WIN_STD_ERROR_HANDLE = -12
_WIN_ERROR_BROKEN_PIPE = 109
_WIN_ERROR_HANDLE_EOF = 38


def _windows_api() -> tuple[Any, Any, Any] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        return ctypes, wintypes, ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return None


def _windows_handle_value(handle: Any, ctypes_module: Any) -> int | None:
    value = getattr(handle, "value", handle)
    if not isinstance(value, int):
        try:
            value = ctypes_module.cast(handle, ctypes_module.c_void_p).value
        except (TypeError, ValueError):
            return None
    invalid = ctypes_module.c_void_p(-1).value
    if value is None or value in {0, invalid}:
        return None
    return int(value)


def _windows_std_handle(selector: int) -> int | None:
    api = _windows_api()
    if api is None:
        return None
    ctypes_module, wintypes, kernel32 = api
    try:
        get_handle = kernel32.GetStdHandle
        get_handle.argtypes = [wintypes.DWORD]
        get_handle.restype = wintypes.HANDLE
        handle = get_handle(selector & 0xFFFFFFFF)
    except (AttributeError, TypeError, ValueError):
        return None
    return _windows_handle_value(handle, ctypes_module)


def _windows_duplicate_std_handle(selector: int) -> int | None:
    source = _windows_std_handle(selector)
    api = _windows_api()
    if source is None or api is None:
        return None
    ctypes_module, wintypes, kernel32 = api
    duplicate = wintypes.HANDLE()
    try:
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        current_process = get_current_process()
        duplicate_handle = kernel32.DuplicateHandle
        duplicate_handle.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes_module.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        duplicate_handle.restype = wintypes.BOOL
        copied = duplicate_handle(
            current_process,
            wintypes.HANDLE(source),
            current_process,
            ctypes_module.byref(duplicate),
            0,
            False,
            0x00000002,
        )
    except (AttributeError, TypeError, ValueError):
        return None
    if not copied:
        return None
    return _windows_handle_value(duplicate, ctypes_module)


def _windows_close_handle(handle: int) -> None:
    api = _windows_api()
    if api is None:
        return
    _ctypes_module, wintypes, kernel32 = api
    try:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(handle))
    except (AttributeError, TypeError, ValueError):
        return


def _windows_write(handle: int, payload: bytes) -> bool:
    api = _windows_api()
    if api is None:
        return False
    ctypes_module, wintypes, kernel32 = api
    offset = 0
    try:
        write_file = kernel32.WriteFile
        write_file.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes_module.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        write_file.restype = wintypes.BOOL
        while offset < len(payload):
            chunk = payload[offset : offset + 65_536]
            buffer = ctypes_module.create_string_buffer(chunk)
            written = wintypes.DWORD(0)
            if not write_file(
                wintypes.HANDLE(handle),
                buffer,
                len(chunk),
                ctypes_module.byref(written),
                None,
            ):
                return False
            count = int(written.value)
            if count <= 0:
                return False
            offset += count
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True


def _windows_read_stdin() -> bytes | None:
    handle = _windows_std_handle(_WIN_STD_INPUT_HANDLE)
    api = _windows_api()
    if handle is None or api is None:
        return None
    ctypes_module, wintypes, kernel32 = api
    chunks: list[bytes] = []
    total = 0
    try:
        read_file = kernel32.ReadFile
        read_file.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes_module.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        read_file.restype = wintypes.BOOL
        while total <= _MAX_REQUEST_BYTES:
            size = min(65_536, _MAX_REQUEST_BYTES + 1 - total)
            buffer = ctypes_module.create_string_buffer(size)
            read = wintypes.DWORD(0)
            if not read_file(
                wintypes.HANDLE(handle),
                buffer,
                size,
                ctypes_module.byref(read),
                None,
            ):
                error = ctypes_module.get_last_error()
                if error in {_WIN_ERROR_BROKEN_PIPE, _WIN_ERROR_HANDLE_EOF}:
                    break
                return None
            count = int(read.value)
            if count <= 0:
                break
            chunks.append(buffer.raw[:count])
            total += count
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    raw = b"".join(chunks)
    if len(raw) > _MAX_REQUEST_BYTES:
        raise _WorkerFailure("oversize")
    return raw


def _decode_request(raw: Any) -> Any:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        raise _WorkerFailure("invalid_response")
    if len(raw) > _MAX_REQUEST_BYTES:
        raise _WorkerFailure("oversize")
    try:
        return json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _WorkerFailure("invalid_response") from None


def _read_stdin() -> Any:
    if bool(getattr(sys, "frozen", False)) and os.name == "nt":
        raw = _windows_read_stdin()
        if raw is None:
            raise _WorkerFailure("network")
        return _decode_request(raw)
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    if stream is not None:
        try:
            raw = stream.read(_MAX_REQUEST_BYTES + 1)
        except (AttributeError, OSError, TypeError, ValueError):
            raw = None
        if raw is not None:
            return _decode_request(raw)
    raw = _windows_read_stdin()
    if raw is None:
        raise _WorkerFailure("network")
    return _decode_request(raw)


def _duplicate_stdout() -> tuple[str, int] | None:
    try:
        sys.stdout.flush()
    except (AttributeError, OSError, ValueError):
        pass
    native = _windows_duplicate_std_handle(_WIN_STD_OUTPUT_HANDLE)
    if native is not None:
        return "windows_handle", native
    try:
        return "fd", os.dup(sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        try:
            return "fd", os.dup(1)
        except OSError:
            return None


def _windows_fd_handle(fd: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        import msvcrt

        return _windows_handle_value(msvcrt.get_osfhandle(fd), ctypes)
    except (ImportError, OSError, ValueError):
        return None


def _windows_set_std_handle(selector: int, handle: int) -> bool:
    api = _windows_api()
    if api is None:
        return False
    _ctypes_module, wintypes, kernel32 = api
    try:
        set_std_handle = kernel32.SetStdHandle
        set_std_handle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        set_std_handle.restype = wintypes.BOOL
        return bool(set_std_handle(selector & 0xFFFFFFFF, wintypes.HANDLE(handle)))
    except (AttributeError, TypeError, ValueError):
        return False


def _silence_streams() -> tuple[Any, int | None, int | None, int | None, int | None]:
    null_stream = open(os.devnull, "wb", buffering=0)
    class _NullText:
        encoding = "utf-8"
        errors = "backslashreplace"

        def write(self, _value: Any) -> int:
            return len(_value) if isinstance(_value, str) else 0

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return False

    sys.stdout = _NullText()  # type: ignore[assignment]
    sys.stderr = _NullText()  # type: ignore[assignment]
    saved_out = None
    saved_err = None
    saved_native_out = None
    saved_native_err = None
    null_handle = _windows_fd_handle(null_stream.fileno())
    if null_handle is not None:
        current_out = _windows_std_handle(_WIN_STD_OUTPUT_HANDLE)
        if current_out is not None and _windows_set_std_handle(_WIN_STD_OUTPUT_HANDLE, null_handle):
            saved_native_out = current_out
        current_err = _windows_std_handle(_WIN_STD_ERROR_HANDLE)
        if current_err is not None and _windows_set_std_handle(_WIN_STD_ERROR_HANDLE, null_handle):
            saved_native_err = current_err
    try:
        saved_out = os.dup(1)
    except OSError:
        saved_out = None
    try:
        os.dup2(null_stream.fileno(), 1)
    except OSError:
        pass
    try:
        saved_err = os.dup(2)
    except OSError:
        saved_err = None
    try:
        os.dup2(null_stream.fileno(), 2)
    except OSError:
        pass
    return null_stream, saved_out, saved_err, saved_native_out, saved_native_err


def _restore_streams(
    null_stream: Any,
    saved_out: int | None,
    saved_err: int | None,
    saved_native_out: int | None,
    saved_native_err: int | None,
) -> None:
    if saved_out is not None:
        try:
            os.close(saved_out)
        finally:
            saved_out = None
    if saved_err is not None:
        try:
            os.close(saved_err)
        finally:
            saved_err = None
    # Keep SDK stdout/stderr attached to NUL for the one-shot child lifetime.
    # Restoring the original buffered Python streams after the protocol frame
    # would leak a delayed SDK print as a second protocol line.
    null_stream.close()


def _emit(payload: bytes, output_target: tuple[str, int] | None) -> bool:
    if output_target is not None:
        target_type, target = output_target
        try:
            if target_type == "windows_handle":
                return _windows_write(target, payload)
            view = memoryview(payload)
            while view:
                written = os.write(target, view)
                if written <= 0:
                    return False
                view = view[written:]
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if target_type == "windows_handle":
                _windows_close_handle(target)
            else:
                os.close(target)
        return True
    stream = getattr(sys.stdout, "buffer", sys.stdout)
    if stream is None or not hasattr(stream, "write"):
        return False
    try:
        try:
            stream.write(payload)
        except TypeError:
            stream.write(payload.decode("utf-8"))
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    output_target = _duplicate_stdout()
    if output_target is None:
        _emit(_encode_frame(_error("network")), None)
        return 1
    (
        null_stream,
        saved_out,
        saved_err,
        saved_native_out,
        saved_native_err,
    ) = _silence_streams()
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    try:
        if args == ["--sdk-info"]:
            try:
                frame = _ok(_sdk_info())
            except _WorkerFailure as exc:
                frame = _error(exc.code)
            except Exception:
                frame = _error("invalid_response")
        elif args:
            frame = _error("invalid_response")
        else:
            try:
                frame = handle_request(_read_stdin())
            except _WorkerFailure as exc:
                frame = _error(exc.code)
            except Exception:
                frame = _error("invalid_response")
        emitted = _emit(_encode_frame(frame), output_target)
        output_target = None
        if not emitted:
            return 1
        return 0 if frame.get("ok") is True else 1
    finally:
        logging.disable(previous_disable)
        _restore_streams(
            null_stream,
            saved_out,
            saved_err,
            saved_native_out,
            saved_native_err,
        )
        if output_target is not None:
            target_type, target = output_target
            if target_type == "windows_handle":
                _windows_close_handle(target)
            else:
                os.close(target)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("PROTOCOL", "handle_request", "main")
