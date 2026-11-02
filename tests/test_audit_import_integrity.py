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
