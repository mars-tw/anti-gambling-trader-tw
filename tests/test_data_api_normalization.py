from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Any

import pytest

from core.data_api.normalization import normalize_closed_dataset
from core.models import Market


def _coverage(count: int) -> dict[str, Any]:
    return {
        "complete": True,
        "pages_complete": True,
        "boundary_complete": True,
        "truncated": False,
        "rejected_rows": 0,
        "duplicate_rows": 0,
        "missing_bars": 0,
        "reasons": [],
        "actual_start_ms": 1_000,
        "actual_end_ms": 2_000,
        "raw_count": count,
    }


def _fill(
    side: str,
    time_ms: int,
    quantity: str,
    quote_amount: str,
    price: str,
    fee_amount: str = "0",
    fee_asset: str | None = None,
    *,
    fill_id: str | int | None = None,
    order_id: str | int | None = None,
) -> dict[str, Any]:
    return {
        "id": f"fill-{time_ms}-{side}" if fill_id is None else fill_id,
        "order_id": f"order-{time_ms}" if order_id is None else order_id,
        "side": side,
        "time_ms": time_ms,
        "symbol": "BTC_USDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "quantity": quantity,
        "quote_amount": quote_amount,
        "price": price,
        "fee_amount": fee_amount,
        "fee_asset": fee_asset,
    }


def _crypto_dataset(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "api-data-v1",
        "provider": "pionex",
        "kind": "fills",
        "market": "spot",
        "symbol": "BTC_USDT",
        "captured_at": "2026-10-08T00:00:00Z",
        "timezone": "UTC",
        "requested": {
            "start_ms": 1_000,
            "end_ms": 2_000,
            "interval": None,
            "limit": 5000,
            "account_ref": "api-account",
        },
        "rows": rows,
        "coverage": _coverage(len(rows)),
        "summary": {},
        "provenance": {
            "data_type": "private-fills",
            "source_scope": "selected-symbol",
            "authentication_verified": True,
        },
    }


def _normalize_crypto(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return normalize_closed_dataset(
        _crypto_dataset(rows),
        opening_zero_confirmed=True,
        transfers_reconciled=True,
    )


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [
                _fill("BUY", 1_000, "1", "100", "100", "1", "USDT"),
                _fill("SELL", 2_000, "1", "120", "120", "1", "USDT"),
            ],
            18.0,
        ),
        (
            [
                _fill("BUY", 1_000, "1", "100", "100", ".01", "BTC"),
                _fill("SELL", 2_000, ".99", "118.8", "120"),
            ],
            18.8,
        ),
        (
            [
                _fill("BUY", 1_000, "1", "100", "100"),
                _fill("SELL", 2_000, ".99", "118.8", "120", ".01", "BTC"),
            ],
            18.8,
        ),
    ],
)
def test_fifo_fee_vectors_are_net_once(rows, expected):
    result = _normalize_crypto(rows)

    assert result["available"] is True
    assert result["reason"] == "ready"
    assert len(result["cycles"]) == 1
    assert len(result["log"].trades) == 1
    trade = result["log"].trades[0]
    assert trade.pnl == pytest.approx(expected)
    assert trade.pnl_is_direct is True
    assert trade.pnl_currency == "USDT"
    assert trade.notional_reliable is False
    assert result["log"].source == "api:pionex:fills"


def test_multiple_fills_in_one_zero_to_zero_cycle_make_one_trade():
    result = _normalize_crypto(
        [
            _fill("BUY", 1_000, ".5", "50", "100"),
            _fill("BUY", 1_100, ".5", "55", "110"),
            _fill("SELL", 2_000, ".4", "48", "120"),
            _fill("SELL", 2_100, ".6", "72", "120"),
        ]
    )

    assert result["available"] is True
    assert len(result["cycles"]) == 1
    assert result["cycles"][0]["fill_count"] == 4
    assert len(result["log"].trades) == 1
    assert result["log"].trades[0].pnl == pytest.approx(15.0)


def test_exact_duplicate_fill_ids_deduplicate_without_using_order_id_as_a_key():
    first_buy = _fill(
        "BUY",
        1_000,
        ".5",
        "50",
        "100",
        fill_id="fill-a",
        order_id="partial-order",
    )
    duplicate_first_buy = dict(first_buy)
    rows = [
        first_buy,
        duplicate_first_buy,
        _fill(
            "BUY",
            1_100,
            ".5",
            "55",
            "110",
            fill_id="fill-b",
            order_id="partial-order",
        ),
        _fill(
            "SELL",
            2_000,
            "1",
            "120",
            "120",
            fill_id="fill-c",
            order_id="partial-order",
        ),
    ]
    dataset = _crypto_dataset(rows)
    dataset["coverage"]["duplicate_rows"] = 1
    dataset["coverage"]["reasons"] = ["duplicate_rows"]
    dataset["coverage"]["raw_count"] = len(rows)

    result = normalize_closed_dataset(
        dataset,
        opening_zero_confirmed=True,
        transfers_reconciled=True,
    )

    assert result["available"] is True
    assert result["reason"] == "ready"
    assert result["cycles"][0]["fill_count"] == 3
    assert len(result["log"].trades) == 1
    assert result["log"].trades[0].pnl == pytest.approx(15.0)


def test_conflicting_same_fill_id_fails_closed_even_with_complete_coverage():
    dataset = _crypto_dataset(
        [
            _fill(
                "BUY",
                1_000,
                "1",
                "100",
                "100",
                fill_id="same-fill",
            ),
            _fill(
                "SELL",
                2_000,
                "1",
                "120",
                "120",
                fill_id="same-fill",
            ),
        ]
    )
    dataset["coverage"]["duplicate_rows"] = 1
    dataset["coverage"]["reasons"] = ["duplicate_rows"]

    result = normalize_closed_dataset(
        dataset,
        opening_zero_confirmed=True,
        transfers_reconciled=True,
    )

    assert result["available"] is False
    assert result["reason"] == "conflicting_fill_id"
    assert result["log"] is None


def test_fill_id_must_be_non_null():
    missing_id = _fill("BUY", 1_000, "1", "100", "100")
    missing_id["id"] = None

    result = _normalize_crypto(
        [
            missing_id,
            _fill("SELL", 2_000, "1", "120", "120"),
        ]
    )

    assert result["available"] is False
    assert result["reason"] == "invalid_fill_rows"


def test_thirty_distinct_partial_fills_with_one_order_id_make_one_cycle():
    rows = [
        _fill(
            "BUY",
            1_000 + index,
            ".1",
            "10",
            "100",
            fill_id=f"buy-{index}",
            order_id="one-order",
        )
        for index in range(15)
    ]
    rows.extend(
        _fill(
            "SELL",
            2_000 + index,
            ".1",
            "12",
            "120",
            fill_id=f"sell-{index}",
            order_id="one-order",
        )
        for index in range(15)
    )

    result = _normalize_crypto(rows)

    assert result["available"] is True
    assert result["cycles"][0]["fill_count"] == 30
    assert len(result["log"].trades) == 1
    assert result["log"].trades[0].pnl == pytest.approx(30.0)


def test_partial_cycle_keeps_fifo_remainder_but_blocks_log():
    result = _normalize_crypto(
        [
            _fill("BUY", 1_000, "2", "200", "100", "2", "USDT"),
            _fill("SELL", 2_000, ".5", "60", "120", ".6", "USDT"),
        ]
    )

    assert result["available"] is False
    assert result["reason"] == "open_inventory"
    assert result["log"] is None
    assert result["unresolved_count"] == 1
    cycle = result["cycles"][0]
    assert cycle["status"] == "open"
    assert cycle["realized_pnl"] == "8.9"
    assert cycle["remaining_quantity"] == "1.5"
    assert cycle["remaining_cost"] == "151.5"
    assert cycle["net_pnl"] is None


def test_opening_and_transfer_attestations_are_both_required():
    dataset = _crypto_dataset(
        [
            _fill("BUY", 1_000, "1", "100", "100"),
            _fill("SELL", 2_000, "1", "120", "120"),
        ]
    )

    no_opening = normalize_closed_dataset(
        dataset,
        opening_zero_confirmed=False,
        transfers_reconciled=True,
    )
    no_transfers = normalize_closed_dataset(
        dataset,
        opening_zero_confirmed=True,
        transfers_reconciled=False,
    )

    assert no_opening["reason"] == "opening_inventory_unconfirmed"
    assert no_transfers["reason"] == "transfers_unreconciled"
    assert no_opening["log"] is None
    assert no_transfers["log"] is None


def test_third_asset_fee_and_same_time_opposite_sides_fail_closed():
    third_fee = _normalize_crypto(
        [
            _fill("BUY", 1_000, "1", "100", "100", "1", "BNB"),
            _fill("SELL", 2_000, "1", "120", "120"),
        ]
    )
    ambiguous = _normalize_crypto(
        [
            _fill("BUY", 1_000, "1", "100", "100"),
            _fill("SELL", 1_000, "1", "120", "120"),
        ]
    )

    assert third_fee["reason"] == "unsupported_fee_asset"
    assert ambiguous["reason"] == "ambiguous_same_time_buy_sell"
    assert third_fee["log"] is None
    assert ambiguous["log"] is None


def test_incomplete_boundary_never_becomes_a_trade_log():
    dataset = _crypto_dataset(
        [
            _fill("BUY", 1_000, "1", "100", "100"),
            _fill("SELL", 2_000, "1", "120", "120"),
        ]
    )
    dataset["coverage"]["boundary_complete"] = False

    result = normalize_closed_dataset(
        dataset,
        opening_zero_confirmed=True,
        transfers_reconciled=True,
    )

    assert result["available"] is False
    assert result["reason"] == "coverage_incomplete"


def _shioaji_dataset() -> dict[str, Any]:
    rows = [
        {
            "symbol": "2330",
            "market": "stock",
            "date": "2026-10-07",
            "time_precision": "date",
            "quantity": "1000",
            "pnl": "-750",
            "fee": "120",
            "tax": "5",
            "currency": "TWD",
            "entry_price": "100",
            "exit_price": "99.25",
            "detail_id": "synthetic-1",
            "details": [],
            "unit": "Share",
        }
    ]
    return {
        "schema_version": "api-data-v1",
        "provider": "shioaji",
        "kind": "realizations",
        "market": "stock",
        "symbol": "2330",
        "captured_at": "2026-10-08T00:00:00+00:00",
        "timezone": "Asia/Taipei",
        "requested": {"start_ms": 1_000, "end_ms": 2_000, "limit": 5000},
        "rows": rows,
        "coverage": _coverage(1),
        "summary": {},
        "provenance": {
            "data_type": "private-realizations",
            "source_scope": "selected-symbol",
            "authentication_verified": True,
        },
    }


def test_shioaji_gross_basis_subtracts_confirmed_fee_and_tax_once():
    result = normalize_closed_dataset(
        _shioaji_dataset(),
        pnl_basis="gross",
        total_costs_confirmed=True,
    )

    assert result["available"] is True
    trade = result["log"].trades[0]
    assert trade.market == Market.TW_STOCK
    assert trade.pnl == pytest.approx(-875.0)
    assert trade.fees == pytest.approx(125.0)
    assert trade.pnl_is_direct is True
    assert trade.entry_time_known is False
    assert trade.exit_time_known is False
    assert trade.side_known is False
    assert trade.pnl_currency == "TWD"


def test_shioaji_requires_explicit_basis_and_total_cost_scope():
    unknown = normalize_closed_dataset(
        _shioaji_dataset(),
        pnl_basis="unknown",
        total_costs_confirmed=True,
    )
    costs_unknown = normalize_closed_dataset(
        _shioaji_dataset(),
        pnl_basis="gross",
        total_costs_confirmed=False,
    )

    assert unknown["reason"] == "pnl_basis_unconfirmed"
    assert costs_unknown["reason"] == "total_costs_unconfirmed"
    assert unknown["log"] is None
    assert costs_unknown["log"] is None


def test_shioaji_missing_settlement_currency_fails_closed_without_twd_default():
    dataset = _shioaji_dataset()
    dataset["rows"][0].pop("currency")

    result = normalize_closed_dataset(
        dataset,
        pnl_basis="gross",
        total_costs_confirmed=True,
    )

    assert result["available"] is False
    assert result["reason"] == "settlement_currency_unavailable"
    assert result["log"] is None


def test_shioaji_explicit_usd_settlement_currency_is_preserved_without_fx():
    dataset = _shioaji_dataset()
    dataset["rows"][0]["currency"] = "USD"

    result = normalize_closed_dataset(
        dataset,
        pnl_basis="gross",
        total_costs_confirmed=True,
    )

    assert result["available"] is True
    assert result["log"].trades[0].pnl == pytest.approx(-875.0)
    assert result["log"].trades[0].pnl_currency == "USD"
    assert result["cycles"][0]["currency"] == "USD"


def test_shioaji_consistent_detail_currencies_can_supply_settlement_currency():
    dataset = _shioaji_dataset()
    dataset["rows"][0].pop("currency")
    dataset["rows"][0]["details"] = [
        {"currency": "USD", "fee_currency": "USD", "tax_currency": "USD"},
        {"pnl_currency": "USD"},
    ]

    result = normalize_closed_dataset(
        dataset,
        pnl_basis="gross",
        total_costs_confirmed=True,
    )

    assert result["available"] is True
    assert result["log"].trades[0].pnl_currency == "USD"


def test_shioaji_mixed_settlement_currencies_fail_closed():
    dataset = _shioaji_dataset()
    dataset["rows"][0]["pnl_currency"] = "USD"

    result = normalize_closed_dataset(
        dataset,
        pnl_basis="gross",
        total_costs_confirmed=True,
    )

    assert result["available"] is False
    assert result["reason"] == "mixed_settlement_currencies"
    assert result["log"] is None


def test_shioaji_explicit_ntd_alias_normalizes_to_twd():
    dataset = _shioaji_dataset()
    dataset["rows"][0]["currency"] = "NTD"

    result = normalize_closed_dataset(
        dataset,
        pnl_basis="gross",
        total_costs_confirmed=True,
    )

    assert result["available"] is True
    assert result["log"].trades[0].pnl_currency == "TWD"
    assert result["cycles"][0]["currency"] == "TWD"


def test_shioaji_fee_or_tax_cross_currency_fails_closed_without_fx():
    dataset = _shioaji_dataset()
    dataset["rows"][0]["fee_currency"] = "USD"

    result = normalize_closed_dataset(
        dataset,
        pnl_basis="gross",
        total_costs_confirmed=True,
    )

    assert result["available"] is False
    assert result["reason"] == "fee_tax_currency_unconfirmed"
    assert result["log"] is None


def test_core_data_api_import_uses_taipei_fallback_without_iana_data():
    script = textwrap.dedent(
        """
        import importlib
        import zoneinfo

        class MissingZoneInfo:
            def __new__(cls, key):
                raise zoneinfo.ZoneInfoNotFoundError(key)

        zoneinfo.ZoneInfo = MissingZoneInfo

        import core.data_api

        normalization = importlib.import_module("core.data_api.normalization")
        normalization = importlib.reload(normalization)
        assert normalization._TAIPEI_IS_FIXED_FALLBACK is True

        dataset = {
            "schema_version": "api-data-v1",
            "provider": "shioaji",
            "kind": "realizations",
            "market": "stock",
            "symbol": "2330",
            "captured_at": "2026-10-08T00:00:00+00:00",
            "timezone": "Asia/Taipei",
            "requested": {"start_ms": 1000, "end_ms": 2000, "limit": 5000},
            "rows": [{
                "symbol": "2330",
                "market": "stock",
                "date": "2026-10-07",
                "time_precision": "date",
                "quantity": "1000",
                "pnl": "-750",
                "fee": "120",
                "tax": "5",
                "currency": "TWD",
                "entry_price": "100",
                "exit_price": "99.25",
                "details": [],
                "unit": "Share",
            }],
            "coverage": {
                "complete": True,
                "pages_complete": True,
                "boundary_complete": True,
                "truncated": False,
                "rejected_rows": 0,
                "duplicate_rows": 0,
                "missing_bars": 0,
                "reasons": [],
                "actual_start_ms": 1000,
                "actual_end_ms": 2000,
                "raw_count": 1,
            },
            "summary": {},
            "provenance": {
                "data_type": "private-realizations",
                "source_scope": "selected-symbol",
                "authentication_verified": True,
            },
        }
        ready = normalization.normalize_closed_dataset(
            dataset,
            pnl_basis="gross",
            total_costs_confirmed=True,
        )
        assert ready["available"] is True
        assert ready["log"].trades[0].entry_time.utcoffset().total_seconds() == 28800

        dataset["rows"][0]["date"] = "1979-01-01"
        historical = normalization.normalize_closed_dataset(
            dataset,
            pnl_basis="gross",
            total_costs_confirmed=True,
        )
        assert historical["available"] is False
        assert historical["reason"] == "taipei_timezone_history_unavailable"
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
