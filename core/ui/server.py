"""Loopback-only HTTP transport for the beginner UI.

The transport intentionally exposes a very small fixed surface.  It is not a
general file server and never accepts filesystem paths from a browser.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import html
import json
import math
import re
import secrets
import socket
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from core.analyzer import sanitize_json
from core.ui.service import Artifact, UIError, UIService

_BIND_HOST = "127.0.0.1"
_MAX_HTTP_BODY = 3 * 1024 * 1024
_READ_TIMEOUT_SECONDS = 0.25
_REQUEST_DEADLINE_SECONDS = 1.0
_HANDLER_JOIN_TIMEOUT_SECONDS = 2.0
_MAX_ACTIVE_HANDLERS = 16
_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_CONTENT_LENGTH = re.compile(r"^(?:0|[1-9][0-9]*)$")
_POST_ROUTES = frozenset(
    {
        "/api/analyze",
        "/api/import-records",
        "/api/record",
        "/api/remove-record",
        "/api/analyze-records",
        "/api/scan",
        "/api/export",
        "/api/scaffold",
        "/api/shutdown",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "symbol",
        "pnl",
        "side",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "quantity",
        "fees",
        "currency",
        "strategy",
    }
)
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)


class _DuplicateJSONKey(ValueError):
    pass


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateJSONKey(f"JSON 欄位重複：{key}")
        out[key] = value
    return out


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 不接受 {value}")


def _finite_json_float(value: str) -> float:
    """Parse a JSON decimal only when Python can represent it finitely."""

    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON 數字必須是有限值")
    return number


def _static_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "static" / name


def _ascii_download_name(filename: str) -> str:
    normalized = unicodedata.normalize("NFKD", filename)
    raw = normalized.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    suffix = Path(filename).suffix.lower()
    ascii_suffix = re.sub(r"[^A-Za-z0-9.]", "", suffix)
    if not safe:
        safe = f"download{ascii_suffix or '.bin'}"
    elif "." not in safe and ascii_suffix:
        safe = f"download{ascii_suffix}"
    return safe[:100]


def _artifact_payload(artifact: Artifact) -> dict[str, str]:
    return {
        "id": artifact.id,
        "filename": artifact.filename,
        "media_type": artifact.media_type,
        "download_url": f"/api/artifact/{artifact.id}",
    }


class _OwnedHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    # Handler threads are deliberately non-daemon: request work must finish or
    # be explicitly interrupted by this server's own socket lifecycle.
    daemon_threads = False
    # ThreadingMixIn's default server_close() joins indefinitely.  We own a
    # bounded registry below instead, so a slow-drip client cannot make local
    # shutdown wait forever before it can close its socket.
    block_on_close = False
    request_queue_size = 16

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._owned_lock = threading.RLock()
        self._owned_closing = False
        self._owned_connections: dict[int, Any] = {}
        self._owned_handler_threads: dict[int, threading.Thread] = {}

    @staticmethod
    def _close_owned_socket(request: Any) -> None:
        try:
            request.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError, ValueError):
            pass
        try:
            request.close()
        except (AttributeError, OSError, ValueError):
            pass

    def process_request(self, request: Any, client_address: Any) -> None:
        """Start only a bounded, tracked handler for this exact server."""

        key = id(request)
        with self._owned_lock:
            if self._owned_closing or len(self._owned_connections) >= _MAX_ACTIVE_HANDLERS:
                reject = True
                worker: threading.Thread | None = None
            else:
                reject = False
                self._owned_connections[key] = request
                worker = threading.Thread(
                    target=self._process_owned_request_thread,
                    args=(request, client_address, key),
                    name=f"ui-request-{key:x}",
                    daemon=False,
                )
                self._owned_handler_threads[key] = worker
        if reject:
            self._close_owned_socket(request)
            return
        try:
            assert worker is not None
            worker.start()
        except BaseException:
            with self._owned_lock:
                self._owned_connections.pop(key, None)
                self._owned_handler_threads.pop(key, None)
            self._close_owned_socket(request)
            raise

    def _expire_owned_connection(self, key: int) -> None:
        """Enforce an absolute deadline even when a peer drips bytes forever."""

        with self._owned_lock:
            request = self._owned_connections.get(key)
        if request is not None:
            self._close_owned_socket(request)

    def _process_owned_request_thread(self, request: Any, client_address: Any, key: int) -> None:
        # The timer only tears down this request socket.  It never writes user
        # data and complements the per-read timeout configured by the handler.
        deadline = threading.Timer(
            _REQUEST_DEADLINE_SECONDS,
            self._expire_owned_connection,
            args=(key,),
        )
        deadline.daemon = True
        deadline.start()
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            deadline.cancel()
            try:
                self.shutdown_request(request)
            except (AttributeError, OSError, ValueError):
                self._close_owned_socket(request)
            finally:
                with self._owned_lock:
                    self._owned_connections.pop(key, None)
                    self._owned_handler_threads.pop(key, None)

    def begin_shutdown(self) -> None:
        """Refuse new owned requests and interrupt every owned socket."""

        with self._owned_lock:
            self._owned_closing = True
            requests = list(self._owned_connections.values())
        for request in requests:
            self._close_owned_socket(request)

    def wait_for_handlers(self, timeout: float) -> bool:
        """Join only this server's workers, and never without a deadline."""

        # Monotonic time keeps this independent of wall-clock changes.
        import time

        until = time.monotonic() + max(0.0, float(timeout))
        current = threading.current_thread()
        with self._owned_lock:
            workers = [
                worker
                for worker in self._owned_handler_threads.values()
                if worker is not current
            ]
        for worker in workers:
            remaining = until - time.monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)
        with self._owned_lock:
            return not any(
                worker.is_alive()
                for worker in self._owned_handler_threads.values()
                if worker is not current
            )

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Never print request data, tokens, temp paths, or tracebacks.
        return


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AntiGamblingTraderUI"
    sys_version = ""

    @property
    def app(self) -> "UIServer":
        return self.server.ui_wrapper  # type: ignore[attr-defined]

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(_READ_TIMEOUT_SECONDS)

    def log_message(self, _format: str, *args: Any) -> None:
        # Request targets can contain secrets on hostile clients.  The UI never
        # logs requests, tokens, filenames, or local paths.
        return

    def log_error(self, _format: str, *args: Any) -> None:
        return

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        status = 405 if code == 501 else (code if code in {400, 403, 404, 405, 409, 413, 422, 500} else 400)
        self._error(status, "不支援或格式不正確的 HTTP 請求")

    def handle_expect_100(self) -> bool:
        self._error(400, "不接受 Expect: 100-continue")
        return False

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Connection", "close")

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        media_type: str,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.close_connection = True
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        try:
            body = json.dumps(
                sanitize_json(payload),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except Exception:
            status = 500
            body = '{"error":"伺服器無法序列化回應"}'.encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        safe_status = status if status in {400, 403, 404, 405, 409, 413, 422, 500} else 500
        self._send_json(safe_status, {"error": str(message)})

    def _validate_target(self) -> str:
        raw = self.path
        if not isinstance(raw, str) or not raw.startswith("/"):
            raise UIError("不支援的請求路徑", status=400)
        split = urlsplit(raw)
        if split.scheme or split.netloc or split.query or split.fragment:
            raise UIError("請求路徑不可包含查詢字串或外部網址", status=400)
        path = split.path
        if "%" in path or "\\" in path or "//" in path or any(
            part in {".", ".."} for part in path.split("/")
        ):
            raise UIError("請求路徑格式不合法", status=400)
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
            raise UIError("請求路徑格式不合法", status=400)
        return path

    def _require_host(self) -> None:
        values = self.headers.get_all("Host") or []
        if len(values) != 1 or values[0] != self.app.host_header:
            raise UIError("Host 不符合本機工作階段", status=403)

    def _require_token(self) -> None:
        values = self.headers.get_all("X-UI-Token") or []
        if len(values) != 1 or not hmac.compare_digest(values[0], self.app.token):
            raise UIError("工作階段驗證失敗", status=403)

    def _require_origin(self) -> None:
        values = self.headers.get_all("Origin") or []
        if len(values) != 1 or values[0] != self.app.url:
            raise UIError("Origin 不符合本機工作階段", status=403)

    def _read_json(self) -> dict[str, Any]:
        if self.headers.get_all("Transfer-Encoding"):
            raise UIError("不接受 Transfer-Encoding", status=400)
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not _CONTENT_LENGTH.fullmatch(lengths[0]):
            raise UIError("必須提供一個有效的 Content-Length", status=400)
        length = int(lengths[0])
        if length > _MAX_HTTP_BODY:
            raise UIError("請求內容超過 3MiB 上限", status=413)
        content_types = self.headers.get_all("Content-Type") or []
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            raise UIError("POST 只接受 application/json", status=400)
        try:
            raw = self.rfile.read(length)
        except socket.timeout as exc:
            raise UIError("讀取請求逾時", status=400) from exc
        if len(raw) != length:
            raise UIError("請求內容長度不完整", status=400)
        try:
            text = raw.decode("utf-8", errors="strict")
            payload = json.loads(
                text,
                object_pairs_hook=_strict_object_pairs,
                parse_constant=_reject_json_constant,
                parse_float=_finite_json_float,
            )
        except (_DuplicateJSONKey, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise UIError(f"JSON 格式錯誤：{exc}", status=400) from exc
        if not isinstance(payload, dict):
            raise UIError("JSON 最外層必須是物件", status=400)
        return payload

    @staticmethod
    def _exact_fields(
        payload: dict[str, Any],
        *,
        required: set[str],
        optional: set[str] | None = None,
    ) -> None:
        allowed = required | (optional or set())
        unknown = sorted(set(payload) - allowed)
        missing = sorted(required - set(payload))
        if unknown:
            raise UIError(f"不接受未知欄位：{', '.join(unknown)}", status=400)
        if missing:
            raise UIError(f"缺少必要欄位：{', '.join(missing)}", status=400)

    def _serve_static(self, path: str) -> bool:
        mapping = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.css": ("app.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        item = mapping.get(path)
        if item is None:
            return False
        name, media_type = item
        try:
            body = _static_path(name).read_bytes()
            if name == "index.html":
                marker = b"__UI_TOKEN__"
                if marker not in body:
                    raise RuntimeError("missing token marker")
                escaped = html.escape(self.app.token, quote=True).encode("ascii")
                body = body.replace(marker, escaped, 1)
        except Exception:
            self._error(500, "介面資源不完整")
            return True
        self._send_bytes(200, body, media_type)
        return True

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._require_host()
            path = self._validate_target()
            if self._serve_static(path):
                return
            if path == "/api/health":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "service": "anti-gambling-trader-ui",
                        "instance_id": self.app.instance_id,
                        "mode": self.app.mode,
                    },
                )
                return
            if path == "/api/state":
                self._require_token()
                self._send_json(200, self.app.service.state())
                return
            prefix = "/api/artifact/"
            if path.startswith(prefix):
                self._require_token()
                artifact_id = path[len(prefix) :]
                if not _ARTIFACT_ID.fullmatch(artifact_id):
                    raise UIError("產物 ID 格式不正確", status=404)
                artifact = self.app.service.artifact(artifact_id)
                fallback = _ascii_download_name(artifact.filename)
                encoded = quote(artifact.filename, safe="")
                disposition = (
                    f'attachment; filename="{fallback}"; '
                    f"filename*=UTF-8''{encoded}"
                )
                self._send_bytes(
                    200,
                    artifact.content,
                    artifact.media_type,
                    [("Content-Disposition", disposition)],
                )
                return
            if path in _POST_ROUTES:
                self._error(405, "此路徑不支援 GET")
            else:
                self._error(404, "找不到指定路徑")
        except UIError as exc:
            self._error(exc.status, str(exc))
        except Exception:
            self._error(500, "伺服器處理請求時發生錯誤")

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_host()
            path = self._validate_target()
            self._require_origin()
            self._require_token()
            payload = self._read_json()

            if path == "/api/analyze":
                if set(payload) == {"sample"}:
                    result = self.app.service.analyze_sample(payload["sample"])
                elif set(payload) == {"filename", "content_base64"}:
                    filename = payload["filename"]
                    encoded = payload["content_base64"]
                    if not isinstance(filename, str) or not isinstance(encoded, str):
                        raise UIError("filename 與 content_base64 必須是文字", status=400)
                    try:
                        content = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise UIError("content_base64 不是嚴格 Base64", status=400) from exc
                    result = self.app.service.analyze_upload(filename, content)
                else:
                    raise UIError(
                        "analyze 必須只提供 sample，或 filename 與 content_base64",
                        status=400,
                    )
                self._send_json(200, result)
                return

            if path == "/api/import-records":
                self._exact_fields(payload, required={"filename", "content_base64"})
                filename = payload["filename"]
                encoded = payload["content_base64"]
                if not isinstance(filename, str) or not isinstance(encoded, str):
                    raise UIError("filename 與 content_base64 必須是文字", status=400)
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise UIError("content_base64 不是嚴格 Base64", status=400) from exc
                self._send_json(200, self.app.service.import_records(filename, content))
                return

            if path == "/api/record":
                unknown = sorted(set(payload) - _RECORD_FIELDS)
                if unknown:
                    raise UIError(f"不接受未知欄位：{', '.join(unknown)}", status=400)
                state = self.app.service.add_record(payload)
                self._send_json(200, state)
                return

            if path == "/api/remove-record":
                self._exact_fields(payload, required={"index", "revision"})
                state = self.app.service.remove_record(
                    payload["index"], payload["revision"]
                )
                self._send_json(200, state)
                return

            if path == "/api/analyze-records":
                self._exact_fields(payload, required=set())
                self._send_json(200, self.app.service.analyze_records())
                return

            if path == "/api/scan":
                self._exact_fields(payload, required={"text"})
                self._send_json(200, self.app.service.scan(payload["text"]))
                return

            if path == "/api/export":
                self._exact_fields(
                    payload,
                    required={"kind"},
                    optional={"analysis_id"},
                )
                artifact = self.app.service.export(
                    payload["kind"], payload.get("analysis_id")
                )
                self._send_json(200, _artifact_payload(artifact))
                return

            if path == "/api/scaffold":
                self._exact_fields(
                    payload,
                    required={"project_name", "symbols"},
                    optional={"market"},
                )
                artifact = self.app.service.scaffold(
                    payload["project_name"],
                    payload["symbols"],
                    payload.get("market", "us_stock"),
                )
                self._send_json(200, _artifact_payload(artifact))
                return

            if path == "/api/shutdown":
                self._exact_fields(payload, required=set())
                self._send_json(200, {"ok": True, "message": "本次使用已結束"})
                threading.Thread(
                    target=self.app.shutdown,
                    name=f"ui-stop-{self.app.instance_id[:8]}",
                    daemon=True,
                ).start()
                return

            if path in {"/", "/app.css", "/app.js", "/api/health", "/api/state"} or path.startswith("/api/artifact/"):
                self._error(405, "此路徑不支援 POST")
            else:
                self._error(404, "找不到指定路徑")
        except UIError as exc:
            self._error(exc.status, str(exc))
        except Exception:
            self._error(500, "伺服器處理請求時發生錯誤")

    def _method_not_allowed(self) -> None:
        try:
            self._require_host()
            self._validate_target()
        except UIError as exc:
            self._error(exc.status, str(exc))
            return
        self._error(405, "不支援此 HTTP 方法")

    do_HEAD = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_TRACE = _method_not_allowed
    do_CONNECT = _method_not_allowed


class UIServer:
    """One isolated loopback server and its session-scoped service."""

    def __init__(
        self,
        httpd: _OwnedHTTPServer,
        service: UIService,
        *,
        mode: str,
        token: str,
        instance_id: str,
    ) -> None:
        self._httpd = httpd
        self.service = service
        self.mode = mode
        self.token = token
        self.instance_id = instance_id
        self.port = int(httpd.server_address[1])
        self.url = f"http://{_BIND_HOST}:{self.port}"
        self.host_header = f"{_BIND_HOST}:{self.port}"
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._serve,
            name=f"ui-http-{instance_id[:8]}",
            daemon=True,
        )

    def _serve(self) -> None:
        try:
            self._httpd.serve_forever(poll_interval=0.1)
        finally:
            # shutdown() owns server_close/service cleanup.
            pass

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._closed.is_set() or self._shutdown_started:
                return
            self._shutdown_started = True
        try:
            # First interrupt only this instance's active sockets.  This wakes
            # incomplete headers/bodies immediately instead of waiting for a
            # per-read timeout or ThreadingMixIn's unbounded join.
            self._httpd.begin_shutdown()
            self._httpd.shutdown()
            self._httpd.server_close()
            if self._thread is not threading.current_thread():
                self._thread.join(timeout=_HANDLER_JOIN_TIMEOUT_SECONDS)
                if self._thread.is_alive():
                    raise RuntimeError("UI HTTP server thread did not stop")
            if not self._httpd.wait_for_handlers(_HANDLER_JOIN_TIMEOUT_SECONDS):
                raise RuntimeError("UI request handlers did not stop")
            self.service.close()
        except BaseException:
            with self._shutdown_lock:
                self._shutdown_started = False
            raise
        else:
            self._closed.set()

    close = shutdown

    def wait(self, timeout: float | None = None) -> bool:
        """Block until this exact server is shut down."""

        return self._closed.wait(timeout)

    def __enter__(self) -> "UIServer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()


def start_server(port: int = 0, mode: str = "browser") -> UIServer:
    """Start one loopback-only UI server on an explicit or ephemeral port."""

    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise UIError("port 必須是 0 到 65535 的整數", status=400)
    if mode not in {"browser", "desktop"}:
        raise UIError("mode 必須是 browser 或 desktop", status=400)
    service = UIService(mode=mode)
    try:
        httpd = _OwnedHTTPServer((_BIND_HOST, port), _Handler)
    except Exception:
        service.close()
        raise
    server = UIServer(
        httpd,
        service,
        mode=mode,
        token=secrets.token_urlsafe(32),
        instance_id=uuid4().hex,
    )
    httpd.ui_wrapper = server  # type: ignore[attr-defined]
    server.start()
    return server


__all__ = ["UIServer", "start_server"]
