"""Offline manager/UI API boundary checks with an in-memory client only."""

from __future__ import annotations

import json
import http.client
import time
from typing import Any

from core.data_api.manager import DataManager
from core.ui.server import start_server


def _dataset(provider: str, *, diagnostic: str | None = None) -> dict[str, Any]:
    summary = {}
    if diagnostic is not None:
        summary["diagnostic"] = diagnostic
    return {
        "schema_version": "api-data-v1",
        "provider": provider,
        "kind": "candles",
        "market": "spot",
        "symbol": "BTCUSDT",
        "captured_at": "2026-10-08T00:00:00Z",
        "timezone": "UTC",
        "requested": {"start_ms": 1_000, "end_ms": 2_000, "interval": "1m", "limit": 10},
        "rows": [],
        "coverage": {
            "complete": True,
            "pages_complete": True,
            "boundary_complete": True,
            "truncated": False,
            "rejected_rows": 0,
            "duplicate_rows": 0,
            "missing_bars": 0,
            "reasons": [],
            "actual_start_ms": None,
            "actual_end_ms": None,
            "raw_count": 0,
        },
        "summary": summary,
        "provenance": {
            "data_type": "synthetic-contract-test",
            "source_scope": "selected-symbol",
            "authentication_verified": True,
        },
    }


class _FakeClient:
    def __init__(self, dataset: dict[str, Any]) -> None:
        self.dataset = dataset

    def fetch(self, _kind: str, _query: dict[str, Any], _context: Any) -> dict[str, Any]:
        return self.dataset


def _wait(manager: DataManager, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        state = manager.status(job_id)
        if state["status"] != "running":
            return state
        time.sleep(0.005)
    raise AssertionError("fake data job did not finish")


def test_public_job_rejects_echo_of_any_session_credential() -> None:
    secret = "session-secret-value-123456"

    def factory(_provider: str, **_kwargs: Any) -> _FakeClient:
        return _FakeClient(_dataset("binance", diagnostic=secret))

    manager = DataManager(client_factory=factory)
    try:
        manager.configure_credentials("binance", api_key="session-key-value-123456", api_secret=secret)
        job = manager.start("binance", "candles", {"symbol": "BTCUSDT", "interval": "1m", "limit": 10})
        state = _wait(manager, job["job_id"])
        serialized = json.dumps(state, ensure_ascii=False)
        assert state["status"] == "failed"
        assert secret not in serialized
    finally:
        manager.close()


def test_manager_rejects_missing_public_credentials_before_start() -> None:
    manager = DataManager(client_factory=lambda *_args, **_kwargs: _FakeClient(_dataset("binance")))
    try:
        try:
            manager.start("binance", "fills", {"symbol": "BTCUSDT", "limit": 10})
        except Exception as exc:
            assert getattr(exc, "code", None) == "auth"
        else:
            raise AssertionError("private data must require credentials")
    finally:
        manager.close()


def test_loopback_api_enforces_host_origin_token_and_schema_without_network() -> None:
    server = start_server(port=0, mode="browser")
    try:
        def request(method: str, path: str, payload: Any = None, *, origin: str | None = None):
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            headers = {"Host": server.host_header, "X-UI-Token": server.token}
            body = None
            if method == "POST":
                headers["Origin"] = server.url if origin is None else origin
                headers["Content-Type"] = "application/json"
                body = json.dumps(payload or {}, separators=(",", ":")).encode("utf-8")
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            connection.close()
            return response.status, raw

        status, body = request("GET", "/api/state")
        assert status == 200
        assert json.loads(body)["data_api"]["providers"] == ["pionex", "binance", "shioaji"]
        status, _ = request("POST", "/api/data-sync", {"provider": "binance", "kind": "candles", "query": {}})
        assert status in {400, 422}
        status, _ = request("POST", "/api/data-sync", {}, origin="http://evil.invalid")
        assert status == 403
    finally:
        server.shutdown()
        assert server.wait(5)
