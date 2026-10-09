"""Offline contract tests for the Pionex/Binance read-only adapters."""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from dataclasses import dataclass
from typing import Any

from core.data_api.binance import BinanceDataClient
from core.data_api.pionex import PionexDataClient
from core.data_api.providers import create_client


@dataclass(frozen=True)
class _Pair:
    api_key: str = "test-key"
    api_secret: str = "test-secret"


class _Context:
    def __init__(self, now_ms: int = 1_700_000_000_000) -> None:
        self.now_ms = now_ms
        self.cancel_event = None
        self.pages = 0
        self.records = 0
        self.sleeps: list[float] = []

    def check(self) -> None:
        return None

    def advance(self, pages: int, records: int) -> None:
        self.pages += pages
        self.records += records

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def remaining_seconds(self) -> float:
        return 45.0


class _Transport:
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def get_json(
        self,
        host: str,
        path: str,
        params: Any = None,
        headers: Any = None,
        *,
        cancel_event: Any = None,
    ) -> Any:
        call = {
            "host": host,
            "path": path,
            "params": dict(params or {}),
            "headers": dict(headers or {}),
            "cancel_event": cancel_event,
        }
        self.calls.append(call)
        return self.responder(call, len(self.calls) - 1)


def _pionex_fill(fill_id: int, time_ms: int) -> dict[str, Any]:
    return {
        "id": str(fill_id),
        "orderId": f"o-{fill_id}",
        "side": "BUY" if fill_id % 2 == 0 else "SELL",
        "timestamp": time_ms,
        "symbol": "BTC_USDT",
        "price": "100.00",
        "size": "0.0100",
        "amount": "1.000000",
        "fee": "0.0010",
        "feeCoin": "USDT",
    }


def _binance_trade(trade_id: int, time_ms: int) -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "id": trade_id,
        "orderId": 10_000 + trade_id,
        "price": "100.00",
        "qty": "0.0100",
        "quoteQty": "1.000000",
        "commission": "0.0010",
        "commissionAsset": "USDT",
        "time": time_ms,
        "isBuyer": True,
    }


def test_factory_selects_all_three_without_optional_sdk_import() -> None:
    transport = _Transport(lambda _call, _index: {})
    assert type(create_client("pionex", transport=transport)).__name__ == "PionexDataClient"
    assert type(create_client("binance", transport=transport)).__name__ == "BinanceDataClient"
    assert type(create_client("shioaji")).__name__ == "ShioajiDataClient"


def test_pionex_candles_preserve_decimal_strings_and_closed_state() -> None:
    now = 1_700_000_000_000
    start = now - 120_000

    def responder(_call: Any, _index: int) -> Any:
        return {
            "result": True,
            "data": {
                "klines": [
                    {
                        "time": start,
                        "open": "100.00",
                        "high": "110.00",
                        "low": "90.00",
                        "close": "105.00",
                        "volume": "12.3400",
                    },
                    {
                        "time": start + 60_000,
                        "open": "105.00",
                        "high": "108.00",
                        "low": "101.00",
                        "close": "107.00",
                        "volume": "2.5000",
                    },
                ]
            },
        }

    context = _Context(now)
    dataset = PionexDataClient(transport=_Transport(responder)).fetch_candles(
        {
            "symbol": "BTC_USDT",
            "market": "spot",
            "interval": "1m",
            "start_ms": start,
            "end_ms": now,
            "limit": 10,
        },
        context,
    )
    assert dataset["schema_version"] == "api-data-v1"
    assert dataset["rows"][0]["open"] == "100.00"
    assert dataset["rows"][0]["volume"] == "12.3400"
    assert dataset["rows"][0]["is_closed"] is True
    assert dataset["provenance"]["authentication_verified"] is False


def test_pionex_signature_is_sorted_unencoded_contract() -> None:
    transport = _Transport(lambda _call, _index: {"result": True, "data": {"fills": []}})
    context = _Context()
    client = PionexDataClient(
        transport=transport,
        credentials=_Pair(),
        clock_ms=lambda: context.now_ms,
    )
    client._private_get(  # noqa: SLF001 - exact signing contract regression
        "/api/v1/trade/fills",
        {"symbol": "BTC_USDT", "startTime": 1, "endTime": 2},
        context,
    )
    call = transport.calls[0]
    expected_params = {
        "endTime": 2,
        "startTime": 1,
        "symbol": "BTC_USDT",
        "timestamp": context.now_ms,
    }
    canonical = "&".join(f"{key}={value}" for key, value in expected_params.items())
    expected = hmac.new(b"test-secret", f"GET/api/v1/trade/fills?{canonical}".encode(), hashlib.sha256).hexdigest()
    assert call["params"] == expected_params
    assert call["headers"]["PIONEX-SIGNATURE"] == expected


def test_pionex_one_millisecond_saturation_is_explicitly_incomplete() -> None:
    moment = 1_700_000_000_000
    rows = [_pionex_fill(index, moment) for index in range(100)]
    transport = _Transport(
        lambda _call, _index: {"result": True, "data": {"fills": rows}}
    )
    context = _Context(moment)
    dataset = PionexDataClient(
        transport=transport,
        credentials=_Pair(),
        clock_ms=lambda: moment,
    ).fetch_fills(
        {
            "symbol": "BTC_USDT",
            "market": "spot",
            "start_ms": moment,
            "end_ms": moment,
            "limit": 500,
        },
        context,
    )
    assert len(dataset["rows"]) == 100
    assert dataset["coverage"]["complete"] is False
    assert dataset["coverage"]["boundary_complete"] is False
    assert "saturated_one_ms_window" in dataset["coverage"]["reasons"]
    assert context.sleeps == [0.55]


def test_binance_candles_report_internal_gaps_and_unclosed_bar() -> None:
    now = 1_700_000_000_000
    start = now - 180_000
    payload = [
        [start, "1.00", "2.00", "0.50", "1.50", "10.00"],
        [start + 120_000, "1.50", "2.50", "1.00", "2.00", "20.00"],
    ]
    dataset = BinanceDataClient(
        transport=_Transport(lambda _call, _index: payload)
    ).fetch_candles(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "interval": "1m",
            "start_ms": start,
            "end_ms": now,
            "limit": 10,
        },
        _Context(now),
    )
    assert dataset["coverage"]["missing_bars"] == 1
    assert dataset["coverage"]["complete"] is False
    assert dataset["rows"][-1]["is_closed"] is True


def test_binance_saturated_history_switches_to_legal_from_id_request() -> None:
    now = 1_700_000_000_000
    start = now - 60_000
    first_page = [_binance_trade(index, start) for index in range(1000)]

    def responder(call: Any, _index: int) -> Any:
        if call["path"] == "/api/v3/time":
            return {"serverTime": now}
        if call["path"] == "/api/v3/exchangeInfo":
            return {
                "symbols": [
                    {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"}
                ]
            }
        if call["path"] == "/api/v3/myTrades" and "startTime" in call["params"]:
            return first_page
        if call["path"] == "/api/v3/myTrades":
            return []
        raise AssertionError(call)

    transport = _Transport(responder)
    dataset = BinanceDataClient(
        transport=transport,
        credentials=_Pair(),
        clock_ms=lambda: now,
    ).fetch_fills(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "start_ms": start,
            "end_ms": now,
            "limit": 2000,
        },
        _Context(now),
    )
    trade_calls = [call for call in transport.calls if call["path"] == "/api/v3/myTrades"]
    assert "startTime" in trade_calls[0]["params"]
    assert "endTime" in trade_calls[0]["params"]
    assert "fromId" not in trade_calls[0]["params"]
    assert trade_calls[1]["params"]["fromId"] == 1000
    assert "startTime" not in trade_calls[1]["params"]
    assert "endTime" not in trade_calls[1]["params"]
    assert len(dataset["rows"]) == 1000
    assert dataset["coverage"]["complete"] is True


def test_binance_signature_covers_exact_urlencoded_query() -> None:
    transport = _Transport(lambda _call, _index: [])
    context = _Context()
    client = BinanceDataClient(
        transport=transport,
        credentials=_Pair(),
        clock_ms=lambda: context.now_ms,
    )
    client._private_get(  # noqa: SLF001 - exact signing contract regression
        "/api/v3/myTrades",
        {"symbol": "BTCUSDT", "fromId": 7, "limit": 1000},
        context,
        0,
    )
    call = transport.calls[0]
    unsigned = [
        ("symbol", "BTCUSDT"),
        ("fromId", 7),
        ("limit", 1000),
        ("recvWindow", 5000),
        ("timestamp", context.now_ms),
    ]
    encoded = urllib.parse.urlencode(unsigned, doseq=False, safe="")
    expected = hmac.new(b"test-secret", encoded.encode(), hashlib.sha256).hexdigest()
    assert call["params"]["signature"] == expected
    assert call["headers"] == {"X-MBX-APIKEY": "test-key"}


def test_binance_account_balances_do_not_fabricate_total_equity() -> None:
    now = 1_700_000_000_000

    def responder(call: Any, _index: int) -> Any:
        if call["path"] == "/api/v3/time":
            return {"serverTime": now}
        return {
            "balances": [
                {"asset": "USDT", "free": "10.00", "locked": "2.00"},
                {"asset": "BTC", "free": "0.0100", "locked": "0.0000"},
            ]
        }

    dataset = BinanceDataClient(
        transport=_Transport(responder),
        credentials=_Pair(),
        clock_ms=lambda: now,
    ).fetch_account({"market": "spot", "symbol": "BTCUSDT"}, _Context(now))
    assert dataset["summary"]["balances"][0]["asset"] == "BTC"
    assert "total_equity" not in dataset["summary"]
    assert dataset["provenance"]["authentication_verified"] is True
