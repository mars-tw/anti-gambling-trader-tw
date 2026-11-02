"""深度稽核回歸測試：新手紀錄的數字精度、檔尾換行與幣別前綴。

這三組都是「不報錯但數字變了」的靜默錯誤，各自對應一個已重現的 bug：

1. 寫檔只保留 6 位有效數字 → 70200.01/70200.02 變成同一個價格，
   載入後 0.01 USDT 的損益變成 0。
2. 舊檔最後一列沒有換行時直接 append → 兩列黏成一列，載入端少掉虧損那筆，
   但新增動作卻回報成功。
3. 幣別字元類別把 USD 排在 USDT 前面 → 「USDT100」被吃掉前綴成「T100」而被拒。

執行: python -m pytest tests/test_audit_onboarding.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ingest.loader import load_trades  # noqa: E402
from core.onboarding import (  # noqa: E402
    BEGINNER_COLUMNS,
    append_beginner_row,
    build_beginner_row,
)


HEADER_LINE = ",".join(BEGINNER_COLUMNS)
# 既有的一筆獲利：直接淨損益 9900 TWD。
EXISTING_ROW = (
    "2330,買,2026-01-05 09:05,2026-01-05 13:20,1000,1010,1000,100,9900,TWD,測試"
)


def _write_without_final_newline(path: Path, encoding: str, newline: str) -> bytes:
    """寫出一個「有效但檔尾沒有換行」的既有檔案，回傳原始位元組。"""

    data = newline.join([HEADER_LINE, EXISTING_ROW]).encode(encoding)
    path.write_bytes(data)
    return data


def _loss_row(strategy: str = "測試") -> dict[str, str]:
    """要被 append 的虧損那筆；黏列時最容易被靜默吃掉。"""

    return build_beginner_row(
        symbol="2454",
        side="買",
        pnl="-500",
        currency="TWD",
        entry_time="2026-01-06 09:10",
        exit_time="2026-01-06 13:25",
        strategy=strategy,
    )


# --- 1. 高精度價格／數量／直接損益的往返 ----------------------------------


def test_high_precision_prices_round_trip_without_losing_cent_pnl(tmp_path):
    row = build_beginner_row(
        symbol="BTCUSDT",
        side="買",
        entry_time="2026-01-05 09:00",
        exit_time="2026-01-05 10:00",
        entry_price="70200.01",
        exit_price="70200.02",
        quantity="1",
        fees="0",
    )
    assert row["進場價"] == "70200.01"
    assert row["出場價"] == "70200.02"
    assert row["數量"] == "1"          # 整數仍沿用舊版無小數點寫法
    assert row["手續費"] == "0"
    assert row["損益幣別"] == "USDT"

    path = tmp_path / "beginner_precision.csv"
    out, count = append_beginner_row(path, row)
    assert count == 1

    log = load_trades(out, auto_estimate_costs=False)
    assert log.rejected_row_count == 0
    trade = log.trades[0]
    assert trade.entry_price == 70200.01
    assert trade.exit_price == 70200.02
    assert trade.quantity == 1.0
    assert trade.pnl == pytest.approx(0.01, abs=1e-9)
    assert trade.pnl > 0
    assert trade.pnl_currency == "USDT"


def test_tiny_quantity_and_cent_direct_pnl_round_trip(tmp_path):
    row = build_beginner_row(
        symbol="BTCUSDT",
        side="買",
        pnl="0.015",
        currency="USDT",
        entry_time="2026-01-05 09:00",
        exit_time="2026-01-05 10:00",
        entry_price="70200.01",
        exit_price="70200.02",
        quantity="1e-8",
        fees="0",
    )
    assert row["損益"] == "0.015"
    assert row["數量"] == "1e-08"
    assert float(row["數量"]) == 1e-8

    path = tmp_path / "beginner_tiny.csv"
    out, _ = append_beginner_row(path, row)

    trade = load_trades(out, auto_estimate_costs=False).trades[0]
    assert trade.pnl == 0.015
    assert trade.quantity == 1e-8
    assert trade.entry_price == 70200.01


# --- 2. 舊檔檔尾缺換行時仍須是兩筆完整紀錄 --------------------------------


@pytest.mark.parametrize(
    "encoding,newline",
    [("utf-8-sig", "\r\n"), ("cp950", "\n")],
)
def test_append_to_file_without_final_newline_keeps_two_intact_rows(
    tmp_path, encoding, newline
):
    path = tmp_path / "beginner_append.csv"
    before = _write_without_final_newline(path, encoding, newline)

    out, count = append_beginner_row(path, _loss_row())
    assert count == 2

    after = path.read_bytes()
    # 既有位元組原封不動，只在尾端補上換行與新列（沒有多出空白交易）。
    assert after.startswith(before)
    added = after[len(before):]
    assert added.startswith(newline.encode("ascii"))
    assert not added.startswith((newline * 2).encode("ascii"))
    assert added.count(b"\n") == 2

    with path.open("r", encoding=encoding, newline="") as handle:
        raw_rows = list(csv.reader(handle))
    assert len(raw_rows) == 3
    assert all(len(values) == len(BEGINNER_COLUMNS) for values in raw_rows)

    log = load_trades(out, auto_estimate_costs=False)
    assert log.rejected_row_count == 0
    assert [trade.symbol for trade in log.trades] == ["2330", "2454"]
    assert sum(trade.pnl for trade in log.trades) == pytest.approx(9400.0)
    assert min(trade.pnl for trade in log.trades) == pytest.approx(-500.0)


def test_cp950_unencodable_row_is_rejected_before_any_byte_is_written(tmp_path):
    path = tmp_path / "beginner_big5.csv"
    before = _write_without_final_newline(path, "cp950", "\n")

    with pytest.raises(ValueError, match="Big5"):
        append_beginner_row(path, _loss_row(strategy="突破📈"))

    # 連分隔換行都不能先落地，否則檔案會留下一列空交易。
    assert path.read_bytes() == before


def test_duplicate_existing_header_is_rejected_instead_of_overwriting_data(tmp_path):
    path = tmp_path / "beginner_duplicate_header.csv"
    before = "\n".join(
        [HEADER_LINE + ",損益", EXISTING_ROW + ",9900"]
    ).encode("utf-8-sig")
    path.write_bytes(before)

    with pytest.raises(ValueError, match="重複"):
        append_beginner_row(path, _loss_row())

    assert path.read_bytes() == before


# --- 3. USDT/USDC 幣別前後綴與中段髒字串 ----------------------------------


@pytest.mark.parametrize(
    "amount,currency",
    [
        ("USDT100", "USDT"),
        ("USDC100", "USDC"),
        ("100USDT", "USDT"),
        ("100 USDT", "USDT"),
    ],
)
def test_crypto_currency_token_prefix_and_suffix_are_canonical(amount, currency):
    row = build_beginner_row(
        symbol="BTCUSDT",
        side="買",
        pnl=amount,
        currency=currency,
        entry_time="2026-01-05 09:00",
        exit_time="2026-01-05 10:00",
    )
    assert row["損益"] == "100"
    assert row["損益幣別"] == currency


def test_prefix_currency_amount_is_its_own_explicit_account_currency():
    row = build_beginner_row(
        symbol="BTCUSDT",
        side="買",
        pnl="USDT100",
        entry_time="2026-01-05 09:00",
        exit_time="2026-01-05 10:00",
    )
    assert row["損益"] == "100"
    assert row["損益幣別"] == "USDT"


@pytest.mark.parametrize(
    "amount",
    ["T100", "USDX100", "1USD00", "10USDT0", "100XUSD", "US DT100"],
)
def test_currency_label_in_the_middle_of_number_is_rejected(amount):
    with pytest.raises(ValueError, match="必須是數字"):
        build_beginner_row(
            symbol="BTCUSDT",
            side="買",
            pnl=amount,
            currency="USDT",
            entry_time="2026-01-05 09:00",
            exit_time="2026-01-05 10:00",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"entry_price": "TWD100", "exit_price": "USD101", "quantity": 10},
        {"entry_price": "USD100", "exit_price": "USD101", "quantity": 10, "fees": "TWD5"},
        {"pnl": "USD100TWD", "currency": "USD"},
        {"pnl": "USD100USD", "currency": "USD"},
        {"pnl": "USD100", "currency": "TWD"},
        {"pnl": "USD100", "quantity": "USDT10"},
    ],
)
def test_raw_currency_annotations_are_rejected_before_a_row_is_returned(kwargs):
    with pytest.raises(ValueError, match="幣別|數量|換算"):
        build_beginner_row(
            symbol="BTCUSDT",
            side="買",
            entry_time="2026-01-05 09:00",
            exit_time="2026-01-05 10:00",
            **kwargs,
        )
