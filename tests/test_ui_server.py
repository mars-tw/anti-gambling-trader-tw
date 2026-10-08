"""Real loopback HTTP tests for the shared browser/desktop transport."""

from __future__ import annotations

import base64
import http.client
import io
import json
import socket
import time
import zipfile

import pytest

from core.ui.server import UIServer, start_server


@pytest.fixture
def ui_server():
    server = start_server(port=0, mode="browser")
    yield server
    server.shutdown()
    assert server.wait(5)


def _request(
    server: UIServer,
    method: str,
    path: str,
    payload=None,
    *,
    token: str | None = None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str = "application/json",
):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    headers = {"Host": host or server.host_header}
    body = None
    if token is not None:
        headers["X-UI-Token"] = token
    if method == "POST":
        headers["Origin"] = origin if origin is not None else server.url
        headers["Content-Type"] = content_type
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    result = (response.status, dict(response.getheaders()), raw)
    conn.close()
    return result


def _json(raw: bytes):
    return json.loads(raw.decode("utf-8"))


def _post(server: UIServer, path: str, payload: dict):
    return _request(server, "POST", path, payload, token=server.token)


def test_static_health_headers_and_no_secret_in_health(ui_server: UIServer):
    status, headers, body = _request(ui_server, "GET", "/")
    assert status == 200
    text = body.decode("utf-8")
    assert "反詐投資王｜新手工作台" in text
    assert ui_server.token in text  # only the same-origin root bootstraps it
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

    status, _, body = _request(ui_server, "GET", "/api/health")
    assert status == 200
    health = _json(body)
    assert health == {
        "ok": True,
        "service": "anti-gambling-trader-ui",
        "instance_id": ui_server.instance_id,
        "mode": "browser",
    }
    assert ui_server.token not in body.decode("utf-8")

    for asset, media in (("/app.css", "text/css"), ("/app.js", "text/javascript")):
        status, headers, body = _request(ui_server, "GET", asset)
        assert status == 200 and body
        assert headers["Content-Type"].startswith(media)


def test_exact_host_token_and_origin(ui_server: UIServer):
    status, _, _ = _request(ui_server, "GET", "/api/health", host=f"localhost:{ui_server.port}")
    assert status == 403
    status, _, _ = _request(ui_server, "GET", "/api/state")
    assert status == 403
    status, _, body = _request(ui_server, "GET", "/api/state", token=ui_server.token)
    assert status == 200
    assert _json(body)["capabilities"]["live_trading"] is False

    status, _, _ = _request(
        ui_server,
        "POST",
        "/api/analyze-records",
        {},
        token=ui_server.token,
        origin="http://localhost:1",
    )
    assert status == 403


def test_fixed_routes_traversal_methods_and_unknown_fields(ui_server: UIServer):
    status, _, _ = _request(ui_server, "GET", "/docs/readme")
    assert status == 404
    status, _, _ = _request(ui_server, "GET", "/api/artifact/%2e%2e/app.js", token=ui_server.token)
    assert status == 400
    status, _, _ = _request(ui_server, "PUT", "/api/state", token=ui_server.token)
    assert status == 405

    status, _, body = _post(
        ui_server,
        "/api/scaffold",
        {"project_name": "x", "symbols": ["AAPL"], "dest": "C:/outside"},
    )
    assert status == 400
    assert "未知" in _json(body)["error"]
    status, _, _ = _post(ui_server, "/api/analyze-records", {"ready": True})
    assert status == 400


def test_analyze_export_download_and_stale_id(ui_server: UIServer):
    status, _, body = _post(ui_server, "/api/analyze", {"sample": "tw"})
    assert status == 200
    analysis = _json(body)
    assert analysis["origin"] == "demo"
    assert analysis["can_live"] is False
    analysis_id = analysis["id"]

    status, _, body = _post(
        ui_server,
        "/api/export",
        {"kind": "json", "analysis_id": analysis_id},
    )
    assert status == 200
    artifact = _json(body)
    assert "content" not in artifact
    assert artifact["download_url"] == f"/api/artifact/{artifact['id']}"

    status, _, _ = _request(ui_server, "GET", artifact["download_url"])
    assert status == 403
    status, headers, raw = _request(
        ui_server,
        "GET",
        artifact["download_url"],
        token=ui_server.token,
    )
    assert status == 200
    assert "filename*=UTF-8''" in headers["Content-Disposition"]
    json.loads(raw.decode("utf-8"))

    status, _, _ = _post(
        ui_server,
        "/api/record",
        {
            "symbol": "AAPL",
            "pnl": "1",
            "side": "unknown",
            "exit_time": "2024-01-02",
            "currency": "USD",
        },
    )
    assert status == 200
    status, _, _ = _post(
        ui_server,
        "/api/export",
        {"kind": "html", "analysis_id": analysis_id},
    )
    assert status == 409


def test_manual_revision_removal_and_artifact_csv(ui_server: UIServer):
    for symbol in ("AAPL", "MSFT"):
        status, _, body = _post(
            ui_server,
            "/api/record",
            {
                "symbol": symbol,
                "pnl": "2",
                "side": "unknown",
                "exit_time": "2024-01-02",
                "currency": "USD",
            },
        )
        assert status == 200
    state = _json(body)
    status, _, _ = _post(
        ui_server,
        "/api/remove-record",
        {"index": 0, "revision": state["manual_revision"] - 1},
    )
    assert status == 409
    status, _, body = _post(
        ui_server,
        "/api/remove-record",
        {"index": 0, "revision": state["manual_revision"]},
    )
    assert status == 200
    assert len(_json(body)["manual_rows"]) == 1

    status, _, body = _post(ui_server, "/api/export", {"kind": "records"})
    artifact = _json(body)
    status, _, raw = _request(
        ui_server, "GET", artifact["download_url"], token=ui_server.token
    )
    assert status == 200
    assert raw.startswith(b"\xef\xbb\xbf")


def test_scaffold_is_download_only_and_paper(ui_server: UIServer):
    status, _, body = _post(
        ui_server,
        "/api/scaffold",
        {"project_name": "paper_demo", "symbols": ["SPY"], "market": "us_stock"},
    )
    assert status == 200
    artifact = _json(body)
    status, _, raw = _request(
        ui_server, "GET", artifact["download_url"], token=ui_server.token
    )
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert all(".." not in name and not name.startswith("/") for name in zf.namelist())
        config = "\n".join(
            zf.read(name).decode("utf-8", "ignore")
            for name in zf.namelist()
            if name.endswith((".yaml", ".py", ".md"))
        )
    assert "broker" in config.lower() or "paper" in config.lower()
    assert "allow_live_trading: true" not in config.lower()


def test_strict_json_duplicate_keys_and_content_type(ui_server: UIServer):
    conn = http.client.HTTPConnection("127.0.0.1", ui_server.port, timeout=10)
    body = b'{"text":"a","text":"b"}'
    conn.request(
        "POST",
        "/api/scan",
        body=body,
        headers={
            "Host": ui_server.host_header,
            "Origin": ui_server.url,
            "X-UI-Token": ui_server.token,
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    response = conn.getresponse()
    response.read()
    assert response.status == 400
    conn.close()

    status, _, _ = _request(
        ui_server,
        "POST",
        "/api/scan",
        {"text": "x"},
        token=ui_server.token,
        content_type="text/plain",
    )
    assert status == 400


def test_duplicate_host_and_content_length_rejected(ui_server: UIServer):
    with socket.create_connection(("127.0.0.1", ui_server.port), timeout=5) as sock:
        request = (
            f"GET /api/health HTTP/1.1\r\n"
            f"Host: {ui_server.host_header}\r\n"
            f"Host: {ui_server.host_header}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        status_line = sock.recv(200).split(b"\r\n", 1)[0]
    assert b" 403 " in status_line

    with socket.create_connection(("127.0.0.1", ui_server.port), timeout=5) as sock:
        body = b"{}"
        request = (
            f"POST /api/analyze-records HTTP/1.1\r\n"
            f"Host: {ui_server.host_header}\r\n"
            f"Origin: {ui_server.url}\r\n"
            f"X-UI-Token: {ui_server.token}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        sock.sendall(request)
        status_line = sock.recv(200).split(b"\r\n", 1)[0]
    assert b" 400 " in status_line


def test_shutdown_joins_partial_header_handler_before_marking_closed():
    server = start_server(mode="browser")
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        sock.sendall(b"GET / HTTP/1.1\r\n")
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with server._httpd._owned_lock:
                if server._httpd._owned_handler_threads:
                    break
            time.sleep(0.01)
        with server._httpd._owned_lock:
            assert server._httpd._owned_handler_threads

        started = time.monotonic()
        server.shutdown()
        elapsed = time.monotonic() - started

        assert elapsed < 2.0
        assert server.wait(0)
        with server._httpd._owned_lock:
            assert not server._httpd._owned_handler_threads
    finally:
        # Keep the peer open through all shutdown assertions above.
        sock.close()
        server.shutdown()


def test_two_instances_are_isolated_and_shutdown_is_owning():
    first = start_server(mode="browser")
    second = start_server(mode="desktop")
    try:
        assert first.port != second.port
        assert first.token != second.token
        assert first.instance_id != second.instance_id
        status, _, _ = _request(second, "GET", "/api/state", token=first.token)
        assert status == 403
        first.shutdown()
        assert first.wait(5)
        status, _, body = _request(second, "GET", "/api/health")
        assert status == 200
        assert _json(body)["instance_id"] == second.instance_id
    finally:
        first.shutdown()
        second.shutdown()


def test_http_shutdown_endpoint_is_idempotent(ui_server: UIServer):
    status, _, body = _post(ui_server, "/api/shutdown", {})
    assert status == 200
    assert _json(body)["ok"] is True
    assert ui_server.wait(10)
    ui_server.shutdown()
