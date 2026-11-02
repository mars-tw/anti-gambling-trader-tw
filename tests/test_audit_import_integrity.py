"""Regression vectors for import ambiguity and labelled-money safety."""

from __future__ import annotations

import json

import pytest

from core.ingest.loader import load_trades


@pytest.mark.parametrize(
    "suffix,delimiter",
    [(".csv", ","), (".tsv", "\t")],
)
def test_normalized_duplicate_headers_are_rejected_before_dict_collapse(tmp_path, suffix, delimiter):
    path = tmp_path / f"duplicate{suffix}"
    path.write_text(
        delimiter.join(["symbol", "pnl", "P N L", "pnl_currency"]) + "\n"
        + delimiter.join(["AAPL", "-1000", "100", "USD"]) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重複"):
        load_trades(path, auto_estimate_costs=False)


def test_json_normalized_duplicate_keys_are_rejected_before_dict_collapse(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text(
        '[{"symbol":"AAPL","pnl":-1000,"P N L":100,"pnl_currency":"USD"}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重複"):
        load_trades(path, auto_estimate_costs=False)


def test_xlsx_normalized_duplicate_headers_are_rejected(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "duplicate.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["symbol", "pnl", "P N L", "pnl_currency"])
    sheet.append(["AAPL", -1000, 100, "USD"])
    workbook.save(path)
    workbook.close()
    with pytest.raises(ValueError, match="重複"):
        load_trades(path, auto_estimate_costs=False)


def test_unique_headers_remain_loadable_and_keep_their_actual_pnl(tmp_path):
    path = tmp_path / "unique.json"
    path.write_text(
        json.dumps([{"symbol": "AAPL", "pnl": "100", "pnl_currency": "USD"}]),
        encoding="utf-8",
    )
    log = load_trades(path, auto_estimate_costs=False)
    assert len(log.trades) == 1
    assert log.trades[0].pnl == 100
    assert log.trades[0].pnl_currency == "USD"


@pytest.mark.parametrize("symbol", ["2330", "BTCUSDT"])
def test_computed_labelled_prices_must_match_the_known_native_quote_unit(tmp_path, symbol):
    path = tmp_path / f"{symbol}.csv"
    path.write_text(
        "symbol,entry_price,exit_price,quantity,side\n"
        f"{symbol},USD100,USD101,10,buy\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="原生報價幣別|沒有任何有效"):
        load_trades(path, auto_estimate_costs=True)


def test_direct_net_pnl_keeps_settlement_amount_but_never_converts_foreign_optional_fee(tmp_path):
    path = tmp_path / "settled-net.csv"
    path.write_text(
        "symbol,net_pnl,fees,pnl_currency\n"
        "2330,TWD100,USD50,TWD\n",
        encoding="utf-8",
    )
    trade = load_trades(path, auto_estimate_costs=False).trades[0]
    assert trade.pnl == 100
    assert trade.pnl_currency == "TWD"
    assert trade.fees == 0


@pytest.mark.parametrize(
    "symbol,foreign_fee",
    [("AAPL", "TWD5"), ("2330", "USD5"), ("BTCUSDT", "USD5")],
)
def test_computed_bare_prices_seed_native_quote_before_foreign_fee_math(tmp_path, symbol, foreign_fee):
    path = tmp_path / f"foreign-fee-{symbol}.csv"
    path.write_text(
        "symbol,entry_price,exit_price,quantity,side,fees\n"
        f"{symbol},100,101,10,buy,{foreign_fee}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="幣別|沒有任何有效"):
        load_trades(path, auto_estimate_costs=True)


@pytest.mark.parametrize(
    "symbol,native_fee,currency",
    [("AAPL", "USD5", "USD"), ("2330", "TWD5", "TWD"), ("BTCUSDT", "USDT5", "USDT")],
)
def test_computed_bare_prices_accept_matching_native_fee(tmp_path, symbol, native_fee, currency):
    path = tmp_path / f"native-fee-{symbol}.csv"
    path.write_text(
        "symbol,entry_price,exit_price,quantity,side,fees\n"
        f"{symbol},100,101,10,buy,{native_fee}\n",
        encoding="utf-8",
    )
    trade = load_trades(path, auto_estimate_costs=False).trades[0]
    assert trade.pnl == 5
    assert trade.pnl_currency == currency


def test_unknown_price_symbol_rejects_computed_pnl_but_preserves_direct_net_without_roi(tmp_path):
    computed = tmp_path / "unknown-price-computed.json"
    computed.write_text(
        json.dumps([{"symbol": "AAPL", "side": "buy", "entry_price": "¥100", "exit_price": "¥101", "quantity": 10}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未明示幣別|沒有任何有效"):
        load_trades(computed, auto_estimate_costs=False)

    direct = tmp_path / "unknown-price-direct.json"
    direct.write_text(
        json.dumps([{"symbol": "AAPL", "side": "buy", "entry_price": "¥100", "exit_price": "¥101", "quantity": 10, "net_pnl": "USD100", "fees": 0}]),
        encoding="utf-8",
    )
    log = load_trades(direct, auto_estimate_costs=False)
    trade = log.trades[0]
    assert trade.pnl == 100
    assert trade.pnl_currency == "USD"
    assert trade.notional_reliable is False
    assert trade.contract_value == 0
    assert "未明示幣別" in log.source
