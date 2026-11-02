"""Synthetic, offline contracts for the data-API regressions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.data_api.binance import BinanceDataClient
from core.data_api.context import SyncContext
from core.data_api.credentials import CredentialPair
from core.data_api.shioaji import ShioajiDataClient
from core.data_api.shioaji_worker import PROTOCOL, handle_request


def _request(kind: str, query: dict, captured_at_ms: int) -> dict:
    return {
        "protocol": PROTOCOL,
        "action": "fetch",
        "kind": kind,
        "query": query,
        "captured_at_ms": captured_at_ms,
        "request_capabilities": {"read_only": True, "allow_orders": False, "allow_ca": False},
        "credentials": {"api_key": "synthetic-key", "api_secret": "synthetic-secret"},
    }


def test_shioaji_account_wire_uses_only_canonical_account_query():
    observed: dict[str, object] = {}

    class StockAccount:
        account_id = "SYNTHETIC-ONLY"
        broker_id = "SYNTHETIC"

    class API:
        stock_account = StockAccount()
        futopt_account = None

        def login(self, **kwargs):
            assert kwargs["subscribe_trade"] is False
            return [self.stock_account]

        def logout(self):
            observed["logout"] = True

        def list_positions(self, **kwargs):
            assert kwargs["account"] is self.stock_account
            assert kwargs["unit"] == "SYNTHETIC_SHARES"
            return [SimpleNamespace(code="2330", quantity="100", direction="Buy")]

        def account_balance(self, **kwargs):
            assert kwargs["account"] is self.stock_account
            return SimpleNamespace(acc_balance="1234.50")

    sdk = SimpleNamespace(
        Shioaji=API,
        Unit=SimpleNamespace(Share="SYNTHETIC_SHARES", Common="SYNTHETIC_COMMON"),
    )

    class LocalClient(ShioajiDataClient):
        def _invoke(self, request, context, **kwargs):
            observed["query"] = request["query"]
            frame = handle_request(request, sdk_module=sdk)
            return self._parse_frame(json.dumps(frame).encode(), b"")

    result = LocalClient(
        credentials=CredentialPair("synthetic-key", "synthetic-secret")
    ).fetch("account", {"market": "stock"}, SyncContext())
    assert observed["query"] == {"market": "stock"}
    assert result["summary"]["cash_balance"] == "1234.50"
    assert result["summary"]["positions"] == [
        {"symbol": "2330", "quantity": "100", "unit": "Share", "cost_known": False, "side": "BUY"}
    ]


def test_shioaji_historical_minute_end_labels_normalize_to_canonical_bar_open():
    taipei = timezone(timedelta(hours=8))
    start = datetime(2026, 10, 30, 9, 0, tzinfo=taipei)

    def epoch_ms(minutes: int = 0, seconds: int = 0) -> int:
        return int((start + timedelta(minutes=minutes, seconds=seconds)).timestamp() * 1000)

    def source_wallclock_ns(minutes: int) -> int:
        return int(
            (start + timedelta(minutes=minutes)).replace(tzinfo=timezone.utc).timestamp()
            * 1_000_000_000
        )

    class StockAccount:
        account_id = "SYNTHETIC-ONLY"
        broker_id = "SYNTHETIC"

    class API:
        stock_account = StockAccount()
        futopt_account = None
        contracts = SimpleNamespace(get=lambda symbol: object() if symbol == "2330" else None)

        def login(self, **kwargs):
            return [self.stock_account]

        def logout(self):
            return None

        def kbars(self, contract, **kwargs):
            return {
                "ts": [source_wallclock_ns(index) for index in range(1, 16)],
                "Open": [str(100 + index) for index in range(1, 16)],
                "High": [str(100 + index) for index in range(1, 16)],
                "Low": [str(100 + index) for index in range(1, 16)],
                "Close": [str(100 + index) for index in range(1, 16)],
                "Volume": [str(index) for index in range(1, 16)],
            }

    sdk = SimpleNamespace(Shioaji=API, __version__="synthetic")
    response = handle_request(
        _request(
            "candles",
            {"symbol": "2330", "market": "stock", "interval": "5m", "start_ms": epoch_ms(), "end_ms": epoch_ms(15), "limit": 100},
            epoch_ms(16),
        ),
        sdk_module=sdk,
    )
    assert response["ok"] is True
    result = response["result"]
    assert result["rows"][:3] == [
        {"time_ms": epoch_ms(), "open": "101", "high": "105", "low": "101", "close": "105", "volume": "15", "is_closed": True},
        {"time_ms": epoch_ms(5), "open": "106", "high": "110", "low": "106", "close": "110", "volume": "40", "is_closed": True},
        {"time_ms": epoch_ms(10), "open": "111", "high": "115", "low": "111", "close": "115", "volume": "65", "is_closed": True},
    ]
    assert result["provenance"]["bar_time_basis"] == "bar_open_epoch_ms_utc"


def test_binance_5000_record_boundary_is_complete_without_network():
    origin = 1_700_000_000_000

    class Transport:
        def __init__(self):
            self.calls: list[dict] = []

        def get_json(self, host, path, params=None, headers=None, **kwargs):
            params = params or {}
            self.calls.append({"path": path, "params": dict(params)})
            if path.endswith("/time"):
                return {"serverTime": origin}
            if path.endswith("/exchangeInfo"):
                return {"symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"}]}
            first = int(params.get("fromId", 0))
            return [
                {
                    "symbol": "BTCUSDT", "id": identifier, "orderId": identifier,
                    "price": "100", "qty": "1", "quoteQty": "100",
                    "commission": "0", "commissionAsset": "USDT",
                    "time": origin + identifier, "isBuyer": True,
                }
                for identifier in range(first, min(first + 1000, 5000))
            ]

    transport = Transport()
    context = SyncContext(now_ms=origin + 10_000)
    client = BinanceDataClient(
        transport=transport,
        credentials=CredentialPair("synthetic-key", "synthetic-secret"),
        clock_ms=lambda: origin,
    )
    result = client.fetch_fills(
        {"symbol": "BTCUSDT", "market": "spot", "start_ms": origin, "end_ms": origin + 9_999, "limit": 5000},
        context,
    )
    assert len(result["rows"]) == 5000
    assert result["coverage"]["complete"] is True
    assert context.records == 5000
