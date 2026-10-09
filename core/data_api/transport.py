"""Read-only HTTPS JSON transport for Pionex / Binance market and account data.

GET only. No request bodies. No order or cancel endpoints. Stdlib only.

Injected ``opener`` may be:
- an urllib-style object with ``open(request, timeout=...)``
- a callable ``opener(request, timeout=...)``

The opener must return a response with ``read([n])``, ``close()``, and
optionally ``geturl()`` / ``status`` / ``getcode()``. Responses are closed
on every path (context manager when available, otherwise ``close()``).
"""

from __future__ import annotations

import json
import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Optional

_MAX_TIMEOUT = 10.0
_DEFAULT_TIMEOUT = 5.0
_DEFAULT_MAX_BYTES = 2097152
_MAX_PARAM_KEY_LEN = 128
_MAX_PARAM_VALUE_LEN = 1024
_MAX_HEADER_NAME_LEN = 128
_MAX_HEADER_VALUE_LEN = 4096
_READ_CHUNK = 65536

_ALLOWED_HOSTS = frozenset(
    {
        "api.pionex.com",
        "data-api.binance.vision",
        "api.binance.com",
    }
)

_PATHS_PIONEX = frozenset(
    {
        "/api/v1/common/symbols",
        "/api/v1/market/klines",
        "/api/v1/market/tickers",
        "/api/v1/trade/fills",
        "/api/v1/account/balances",
    }
)

_PATHS_BINANCE_MARKET = frozenset(
    {
        "/api/v3/klines",
        "/api/v3/ticker/price",
        "/api/v3/exchangeInfo",
        "/api/v3/time",
    }
)

_PATHS_BINANCE_API = frozenset(
    {
        "/api/v3/time",
        "/api/v3/account",
        "/api/v3/myTrades",
    }
)

_HOST_PATHS = {
    "api.pionex.com": _PATHS_PIONEX,
    "data-api.binance.vision": _PATHS_BINANCE_MARKET,
    "api.binance.com": _PATHS_BINANCE_API,
}

_ALLOWED_HEADER_NAMES = frozenset(
    {
        "accept",
        "user-agent",
        "pionex-key",
        "pionex-signature",
        "x-mbx-apikey",
    }
)

_PIONEX_CRED_HEADERS = frozenset({"pionex-key", "pionex-signature"})
_BINANCE_CRED_HEADERS = frozenset({"x-mbx-apikey"})

_PIONEX_PRIVATE_PATHS = frozenset(
    {
        "/api/v1/trade/fills",
        "/api/v1/account/balances",
    }
)

_BINANCE_PRIVATE_PATHS = frozenset(
    {
        "/api/v3/account",
        "/api/v3/myTrades",
    }
)

_USER_MESSAGES = {
    "auth": "認證失敗",
    "rate_limit": "請求過於頻繁",
    "timeout": "連線逾時",
    "network": "網路錯誤",
    "invalid_response": "回應格式無效",
    "oversize": "回應過大",
    "cancelled": "已取消",
}

_SAFE_CODES = frozenset(_USER_MESSAGES)


def _user_message(code: str) -> str:
    return _USER_MESSAGES.get(code, _USER_MESSAGES["invalid_response"])


class DataAPIError(Exception):
    """Sanitized transport failure. ``str`` / ``repr`` never include URL, body, or headers."""

    def __init__(self, code: str, status: Optional[int] = None, message: Optional[str] = None):
        safe_code = code if code in _SAFE_CODES else "invalid_response"
        safe_status: Optional[int]
        if status is None or isinstance(status, bool):
            safe_status = None
        elif isinstance(status, int):
            safe_status = status
        else:
            safe_status = None
        # Caller-supplied message is ignored so raw HTTP text cannot leak.
        safe_message = _user_message(safe_code)
        self.code = safe_code
        self.status = safe_status
        self.message = safe_message
        super().__init__(safe_message)

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"DataAPIError(code={self.code!r}, status={self.status!r})"


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _close_quietly(fp)
        status = code if isinstance(code, int) and not isinstance(code, bool) else None
        raise DataAPIError("invalid_response", status=status) from None


def _default_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(),
        _RejectRedirectHandler(),
    )


def _is_cancelled(cancel_event: Any) -> bool:
    if cancel_event is None:
        return False
    is_set = getattr(cancel_event, "is_set", None)
    if callable(is_set):
        try:
            return bool(is_set())
        except DataAPIError:
            raise
        except Exception:
            return False
    return bool(cancel_event)


def _check_cancel(cancel_event: Any) -> None:
    if _is_cancelled(cancel_event):
        raise DataAPIError("cancelled")


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return True
    return math.isfinite(value)


def _validate_timeout(timeout: Any) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout")
    value = float(timeout)
    if not math.isfinite(value) or not (0.0 < value <= _MAX_TIMEOUT):
        raise ValueError("timeout")
    return value


def _validate_max_bytes(max_bytes: Any) -> int:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise ValueError("max_bytes")
    if max_bytes < 1:
        raise ValueError("max_bytes")
    return max_bytes


def _looks_like_url(value: str) -> bool:
    lowered = value.lower()
    return "://" in value or lowered.startswith("http:") or lowered.startswith("https:")


def _validate_host(host: Any) -> str:
    if not isinstance(host, str) or _looks_like_url(host):
        raise DataAPIError("invalid_response")
    if host != host.strip() or not host:
        raise DataAPIError("invalid_response")
    if any(ch in host for ch in ("/", "\\", "?", "#", "@", ":", " ", "\t", "\r", "\n")):
        raise DataAPIError("invalid_response")
    if host not in _ALLOWED_HOSTS:
        raise DataAPIError("invalid_response")
    return host


def _validate_path(host: str, path: Any) -> str:
    if not isinstance(path, str) or _looks_like_url(path):
        raise DataAPIError("invalid_response")
    if path != path.strip() or not path.startswith("/") or path.endswith("/"):
        # Allowlist entries have no trailing slash; reject variants.
        if path not in _HOST_PATHS.get(host, frozenset()):
            raise DataAPIError("invalid_response")
    if any(ch in path for ch in ("?", "#", "\\", " ", "\t", "\r", "\n")):
        raise DataAPIError("invalid_response")
    if "//" in path or ".." in path:
        raise DataAPIError("invalid_response")
    allowed = _HOST_PATHS.get(host)
    if allowed is None or path not in allowed:
        raise DataAPIError("invalid_response")
    return path


def _scalar_query_value(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise DataAPIError("invalid_response")
    if isinstance(value, str):
        if len(value) > _MAX_PARAM_VALUE_LEN or "\x00" in value:
            raise DataAPIError("invalid_response")
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataAPIError("invalid_response")
        return repr(value) if value != value // 1 else str(int(value)) if value.is_integer() else format(value, ".15g")
    raise DataAPIError("invalid_response")


def _encode_params(params: Any) -> str:
    if params is None:
        return ""
    if not isinstance(params, Mapping):
        raise DataAPIError("invalid_response")
    pairs = []
    for key, value in params.items():
        if not isinstance(key, str) or not key or len(key) > _MAX_PARAM_KEY_LEN:
            raise DataAPIError("invalid_response")
        if key != key.strip() or any(ch in key for ch in ("&", "=", "?", "#", "\r", "\n", "\x00")):
            raise DataAPIError("invalid_response")
        pairs.append((key, _scalar_query_value(value)))
    return urllib.parse.urlencode(pairs, doseq=False, safe="")


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _credential_header_allowed(lower_name: str, host: str, path: str) -> bool:
    if lower_name in _PIONEX_CRED_HEADERS:
        return host == "api.pionex.com" and path in _PIONEX_PRIVATE_PATHS
    if lower_name in _BINANCE_CRED_HEADERS:
        return host == "api.binance.com" and path in _BINANCE_PRIVATE_PATHS
    return True


def _validate_headers(headers: Any, host: str, path: str) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise DataAPIError("invalid_response")
    out: dict[str, str] = {}
    seen_lower: set[str] = set()
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise DataAPIError("invalid_response")
        if not name or len(name) > _MAX_HEADER_NAME_LEN or len(value) > _MAX_HEADER_VALUE_LEN:
            raise DataAPIError("invalid_response")
        if ":" in name or _has_control_chars(name) or _has_control_chars(value):
            raise DataAPIError("invalid_response")
        lower = name.lower()
        if lower in seen_lower:
            raise DataAPIError("invalid_response")
        seen_lower.add(lower)
        if lower not in _ALLOWED_HEADER_NAMES:
            raise DataAPIError("invalid_response")
        if not _credential_header_allowed(lower, host, path):
            raise DataAPIError("invalid_response")
        out[name] = value
    return out


def _build_url(host: str, path: str, query: str) -> str:
    return urllib.parse.urlunparse(("https", host, path, "", query, ""))


def _response_status(response: Any) -> Optional[int]:
    status = getattr(response, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        try:
            code = getcode()
        except Exception:
            return None
        if isinstance(code, int) and not isinstance(code, bool):
            return code
    return None


def _raise_http_status(status: Optional[int]) -> None:
    if status in (401, 403):
        raise DataAPIError("auth", status=status)
    if status == 429:
        raise DataAPIError("rate_limit", status=status)
    raise DataAPIError("invalid_response", status=status)


def _is_timeout_exc(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return False


def _close_quietly(response: Any) -> None:
    if response is None:
        return
    closer = getattr(response, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


class _ResponseCloser:
    def __init__(self, response: Any):
        self._response = response
        self._closed = False

    def __enter__(self) -> Any:
        entered = self._response
        if hasattr(entered, "__enter__") and hasattr(entered, "__exit__"):
            try:
                entered = entered.__enter__()
            except DataAPIError:
                self._closed = True
                raise
            except Exception as exc:
                self._closed = True
                _close_quietly(self._response)
                if _is_timeout_exc(exc):
                    raise DataAPIError("timeout") from None
                raise DataAPIError("network") from None
        return entered

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        if self._closed:
            return False
        self._closed = True
        response = self._response
        if hasattr(response, "__exit__"):
            try:
                response.__exit__(exc_type, exc, tb)
            except Exception:
                _close_quietly(response)
        else:
            _close_quietly(response)
        return False


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite")
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite(item)
    elif isinstance(value, list):
        for item in value:
            _reject_nonfinite(item)


def _object_pairs_hook(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    out: dict[Any, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate")
        out[key] = value
    return out


def _parse_constant(_value: str) -> Any:
    raise ValueError("nonfinite")


def _parse_json(raw: bytes) -> Any:
    if not raw:
        raise DataAPIError("invalid_response")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DataAPIError("invalid_response") from None
    try:
        parsed = json.loads(
            text,
            parse_constant=_parse_constant,
            object_pairs_hook=_object_pairs_hook,
        )
        _reject_nonfinite(parsed)
    except DataAPIError:
        raise
    except Exception:
        raise DataAPIError("invalid_response") from None
    if not isinstance(parsed, (dict, list)):
        raise DataAPIError("invalid_response")
    return parsed


class ReadOnlyHTTPTransport:
    """HTTPS GET JSON client with host/path allowlists and sanitized errors."""

    def __init__(
        self,
        timeout: float = _DEFAULT_TIMEOUT,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        opener: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._timeout = _validate_timeout(timeout)
        self._max_bytes = _validate_max_bytes(max_bytes)
        self._opener = opener
        if not callable(clock):
            raise ValueError("clock")
        self._clock = clock

    def get_json(
        self,
        host: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        *,
        cancel_event: Any = None,
    ) -> Any:
        _check_cancel(cancel_event)
        started = self._clock()
        self._enforce_deadline(started)
        safe_host = _validate_host(host)
        safe_path = _validate_path(safe_host, path)
        query = _encode_params(params)
        url = _build_url(safe_host, safe_path, query)
        header_map = _validate_headers(headers, safe_host, safe_path)
        request = urllib.request.Request(url, data=None, method="GET")
        for name, value in header_map.items():
            request.add_header(name, value)
        _check_cancel(cancel_event)
        self._enforce_deadline(started)
        response = None
        try:
            response = self._open(request, started)
            _check_cancel(cancel_event)
            self._enforce_deadline(started)
            with _ResponseCloser(response) as body:
                response = None
                self._reject_redirected_url(body, url)
                status = _response_status(body)
                if status is not None and status != 200:
                    _raise_http_status(status)
                raw = self._read_capped(body, started, cancel_event)
                _check_cancel(cancel_event)
                self._enforce_deadline(started)
                parsed = _parse_json(raw)
                _check_cancel(cancel_event)
                self._enforce_deadline(started)
                return parsed
        except DataAPIError:
            raise
        except urllib.error.HTTPError as exc:
            status = getattr(exc, "code", None)
            if not isinstance(status, int) or isinstance(status, bool):
                status = _response_status(exc)
            _close_quietly(exc)
            if status in (401, 403):
                raise DataAPIError("auth", status=status) from None
            if status == 429:
                raise DataAPIError("rate_limit", status=status) from None
            raise DataAPIError("invalid_response", status=status) from None
        except urllib.error.URLError as exc:
            if _is_timeout_exc(exc):
                raise DataAPIError("timeout") from None
            raise DataAPIError("network") from None
        except (TimeoutError, socket.timeout):
            raise DataAPIError("timeout") from None
        except OSError:
            raise DataAPIError("network") from None
        except Exception:
            raise DataAPIError("network") from None
        finally:
            _close_quietly(response)

    def _remaining(self, started: float) -> float:
        left = self._timeout - (self._clock() - started)
        if not math.isfinite(left) or left <= 0.0:
            raise DataAPIError("timeout")
        return left

    def _enforce_deadline(self, started: float) -> None:
        self._remaining(started)

    def _open(self, request: urllib.request.Request, started: float) -> Any:
        timeout = self._remaining(started)
        opener = self._opener if self._opener is not None else _default_opener()
        try:
            if hasattr(opener, "open") and callable(opener.open):
                return opener.open(request, timeout=timeout)
            if callable(opener):
                return opener(request, timeout=timeout)
        except DataAPIError:
            raise
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            if _is_timeout_exc(exc):
                raise DataAPIError("timeout") from None
            raise DataAPIError("network") from None
        except (TimeoutError, socket.timeout):
            raise DataAPIError("timeout") from None
        except Exception:
            raise DataAPIError("network") from None
        raise DataAPIError("network") from None

    def _reject_redirected_url(self, response: Any, expected: str) -> None:
        geturl = getattr(response, "geturl", None)
        if not callable(geturl):
            return
        try:
            actual = geturl()
        except DataAPIError:
            raise
        except Exception:
            raise DataAPIError("invalid_response") from None
        if actual is None:
            return
        if not isinstance(actual, str):
            raise DataAPIError("invalid_response")
        if actual == "":
            return
        if actual != expected:
            raise DataAPIError("invalid_response")

    def _read_capped(self, response: Any, started: float, cancel_event: Any = None) -> bytes:
        read = getattr(response, "read", None)
        if not callable(read):
            raise DataAPIError("invalid_response")
        limit = self._max_bytes + 1
        buf = bytearray()
        try:
            while len(buf) < limit:
                _check_cancel(cancel_event)
                self._enforce_deadline(started)
                n = min(_READ_CHUNK, limit - len(buf))
                chunk = read(n)
                _check_cancel(cancel_event)
                self._enforce_deadline(started)
                if chunk is None or chunk == b"":
                    break
                if isinstance(chunk, str):
                    raise DataAPIError("invalid_response")
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise DataAPIError("invalid_response")
                buf.extend(chunk)
        except DataAPIError:
            raise
        except (TimeoutError, socket.timeout):
            raise DataAPIError("timeout") from None
        except OSError:
            raise DataAPIError("network") from None
        except Exception:
            raise DataAPIError("invalid_response") from None
        if len(buf) > self._max_bytes:
            raise DataAPIError("oversize")
        return bytes(buf)


__all__ = ("DataAPIError", "ReadOnlyHTTPTransport")
