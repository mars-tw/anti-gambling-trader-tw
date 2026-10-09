"""Offline fake-SDK and owned-child tests for the Shioaji data adapter."""

from __future__ import annotations

import subprocess
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from core.data_api.shioaji import ShioajiDataClient
from core.data_api.shioaji import _cleanup_owned_temp, _new_owned_temp
from core.data_api.shioaji_worker import PROTOCOL, _sdk_info, _timestamp_ms, handle_request
from core.data_api.transport import DataAPIError


@dataclass(frozen=True)
class _Pair:
    api_key: str = "worker-test-key"
    api_secret: str = "worker-test-secret"


class StockAccount:
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self.broker_id = "B001"
        self.person_id = "PERSON"


class FutureAccount:
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self.broker_id = "F001"


class _Contracts:
    def __init__(self) -> None:
        self.contract = object()

    def get(self, symbol: str) -> Any:
        return self.contract if symbol == "2330" else None


class _FakeAPI:
    def __init__(self, stock_accounts: list[StockAccount] | None = None) -> None:
        self.accounts = stock_accounts or [StockAccount("12345678")]
        self.stock_account = self.accounts[0]
        self.futopt_account = FutureAccount("87654321")
        self.contracts = _Contracts()
        self.Contracts = SimpleNamespace(Stocks={}, Futures={})
        self.login_kwargs: dict[str, Any] | None = None
        self.logout_called = False
        self.positions_called = 0
        self.balance_called = 0
        self.profit_called = 0
        self.detail_called = 0

    def login(self, **kwargs: Any) -> list[Any]:
        self.login_kwargs = kwargs
        return [*self.accounts, self.futopt_account]

    def logout(self) -> None:
        self.logout_called = True

    def kbars(self, contract: Any, **kwargs: Any) -> dict[str, list[Any]]:
        assert contract is self.contracts.contract
        assert kwargs["timeout"] == 5000
        start = 1_779_094_860_000_000_000
        return {
            "ts": [start, start + 60_000_000_000],
            "Open": ["100.00", "101.00"],
            "High": ["102.00", "103.00"],
            "Low": ["99.00", "100.00"],
            "Close": ["101.00", "102.00"],
            "Volume": ["10", "20"],
        }

    def list_positions(self, **kwargs: Any) -> list[Any]:
        self.positions_called += 1
        assert kwargs["timeout"] == 5000
        return [SimpleNamespace(code="2330", quantity="100", direction="Buy")]

    def account_balance(self, **kwargs: Any) -> Any:
        self.balance_called += 1
        assert kwargs["timeout"] == 5000
        return SimpleNamespace(acc_balance="50000.00")

    def list_profit_loss(self, **kwargs: Any) -> list[Any]:
        self.profit_called += 1
        assert kwargs["timeout"] == 5000
        return [
            SimpleNamespace(
                id=7,
                code="2330",
                quantity="1000",
                pnl="-750",
                fee="120",
                tax="5",
                date="2026-05-18",
            )
        ]

    def list_profit_loss_detail(self, **kwargs: Any) -> list[Any]:
        self.detail_called += 1
        assert kwargs["detail_id"] == 7
        assert kwargs["timeout"] == 5000
        return []


def _sdk(api: _FakeAPI) -> Any:
    return SimpleNamespace(
        __version__="1.7.7",
        Shioaji=lambda: api,
        Unit=SimpleNamespace(Share="SHARE-UNIT", Common="COMMON-UNIT"),
    )


def _request(kind: str, *, account_ref: str | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {
        "symbol": "2330",
        "market": "stock",
        "interval": "1m",
        "start_ms": 1_779_066_060_000,
        "end_ms": 1_779_066_180_000,
        "limit": 2000,
    }
    if account_ref is not None:
        query["account_ref"] = account_ref
    return {
        "protocol": PROTOCOL,
        "action": "fetch",
        "kind": kind,
        "query": query,
        "captured_at_ms": 1_779_066_180_000,
        "request_capabilities": {
            "read_only": True,
            "allow_orders": False,
            "allow_ca": False,
        },
        "credentials": {"api_key": "fake-key", "api_secret": "fake-secret"},
    }


def test_sdk_info_is_metadata_only_and_does_not_construct_client() -> None:
    calls = {"factory": 0}

    def factory() -> Any:
        calls["factory"] += 1
        return _FakeAPI()

    info = _sdk_info(SimpleNamespace(__version__="1.7.7", Shioaji=factory))
    assert info["version"] == "1.7.7"
    assert info["login_performed"] is False
    assert calls["factory"] == 0


def test_worker_login_is_read_only_and_kbar_nanoseconds_become_milliseconds() -> None:
    api = _FakeAPI()
    frame = handle_request(_request("candles"), sdk_module=_sdk(api))
    assert frame["ok"] is True
    assert api.login_kwargs == {
        "api_key": "fake-key",
        "secret_key": "fake-secret",
        "subscribe_trade": False,
        "receive_window": 30000,
        "force_refresh": False,
    }
    dataset = frame["result"]
    assert dataset["rows"][0]["time_ms"] == 1_779_066_060_000
    assert dataset["rows"][0]["open"] == "100.00"
    assert dataset["timezone"] == "Asia/Taipei"
    assert api.logout_called is True


def test_historical_ns_golden_value_decodes_to_exact_utc_milliseconds() -> None:
    assert _timestamp_ms(1_779_094_860_000_000_000) == 1_779_066_060_000


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL behavior")
def test_windows_runtime_acl_uses_current_token_under_hostile_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERNAME", "Everyone")
    monkeypatch.setenv("PATH", "C:\\hostile\\does-not-exist")
    owned = _new_owned_temp()
    try:
        assert owned.exists()
    finally:
        assert _cleanup_owned_temp(owned) is True
        assert not owned.exists()


def test_worker_kbar_query_filters_to_requested_utc_window() -> None:
    api = _FakeAPI()
    request = _request("candles")
    request["query"]["start_ms"] = 1_779_066_120_000
    frame = handle_request(request, sdk_module=_sdk(api))
    assert frame["ok"] is True
    assert [row["time_ms"] for row in frame["result"]["rows"]] == [1_779_066_120_000]


def test_multiple_matching_accounts_require_opaque_selection() -> None:
    api = _FakeAPI([StockAccount("11112222"), StockAccount("33334444")])
    frame = handle_request(_request("account"), sdk_module=_sdk(api))
    assert frame["ok"] is True
    dataset = frame["result"]
    assert dataset["coverage"]["complete"] is False
    assert dataset["coverage"]["reasons"] == ["account_selection_required"]
    accounts = dataset["summary"]["accounts"]
    assert len(accounts) == 2
    assert all(item["ref"].startswith("acct_") for item in accounts)
    serialized = str(dataset)
    assert "11112222" not in serialized
    assert "33334444" not in serialized
    assert api.positions_called == 0
    assert api.balance_called == 0


def test_selected_account_uses_share_unit_and_never_exports_full_identifier() -> None:
    first_api = _FakeAPI([StockAccount("11112222"), StockAccount("33334444")])
    first = handle_request(_request("account"), sdk_module=_sdk(first_api))
    selected_ref = first["result"]["summary"]["accounts"][1]["ref"]
    second_api = _FakeAPI([StockAccount("11112222"), StockAccount("33334444")])
    frame = handle_request(
        _request("account", account_ref=selected_ref),
        sdk_module=_sdk(second_api),
    )
    assert frame["ok"] is True
    summary = frame["result"]["summary"]
    assert summary["positions"] == [
        {
            "symbol": "2330",
            "quantity": "100",
            "unit": "Share",
            "cost_known": False,
            "side": "BUY",
        }
    ]
    assert summary["cash_balance"] == "50000.00"
    assert "33334444" not in str(frame)
    assert second_api.positions_called == 1
    assert second_api.balance_called == 1


def test_realization_keeps_gross_pnl_fee_tax_and_date_only_flags() -> None:
    api = _FakeAPI()
    frame = handle_request(_request("fills"), sdk_module=_sdk(api))
    assert frame["ok"] is True
    dataset = frame["result"]
    assert dataset["kind"] == "realizations"
    row = dataset["rows"][0]
    assert row["pnl"] == "-750"
    assert row["fee"] == "120"
    assert row["tax"] == "5"
    assert row["time_precision"] == "date"
    assert dataset["summary"]["intraday_time_known"] is False
    assert dataset["summary"]["execution_order_known"] is False
    assert api.detail_called == 1


class _CancelContext:
    cancel_event = None
    now_ms = 1_700_000_000_000

    def __init__(self) -> None:
        self.checks = 0

    def check(self) -> None:
        self.checks += 1
        if self.checks >= 3:
            raise DataAPIError("cancelled")

    def remaining_seconds(self) -> float:
        return 45.0

    def advance(self, pages: int, records: int) -> None:
        raise AssertionError((pages, records))


class _HangingProcess:
    def __init__(self) -> None:
        self.killed = False
        self.communicate_calls = 0

    def communicate(self, input: Any = None, timeout: float | None = None) -> tuple[bytes, bytes]:
        self.communicate_calls += 1
        if self.killed:
            return b"", b""
        raise subprocess.TimeoutExpired("worker", timeout)

    def poll(self) -> int | None:
        return 0 if self.killed else None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.killed = True
        return 0


def test_parent_cancel_kills_and_joins_only_its_owned_child() -> None:
    process = _HangingProcess()
    capture: dict[str, Any] = {}

    def popen(command: list[str], **kwargs: Any) -> _HangingProcess:
        capture["command"] = command
        capture["kwargs"] = kwargs
        return process

    client = ShioajiDataClient(credentials=_Pair(), popen_factory=popen)
    with pytest.raises(DataAPIError) as caught:
        client.fetch_candles(
            {
                "symbol": "2330",
                "market": "stock",
                "interval": "1m",
                "start_ms": 1_699_999_800_000,
                "end_ms": 1_700_000_000_000,
                "limit": 10,
            },
            _CancelContext(),
        )
    assert caught.value.code == "cancelled"
    assert process.killed is True
    assert capture["command"][1:3] == ["-m", "core.data_api.shioaji_worker"]
    assert "worker-test-key" not in " ".join(capture["command"])
    assert all("worker-test-key" not in value for value in capture["kwargs"]["env"].values())
