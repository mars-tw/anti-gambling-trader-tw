"""Deep-audit regressions for ingestion, integrity and numerical safety."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from core.analyzer import analyze_file
from core.antiscam.guru_claim import analyze_guru_claim
from core.backtest.validate import holdout_validate
from core.ingest.loader import _rows_from_json, _to_float, load_trades
from core.models import Market, Side, Trade, TradeLog
from core.report_html import render_html_report, render_share_card
from core.verdict.judge import VerdictLevel, judge
from core.verdict.statistics import (
    required_sample_size_from_pnls,
    test_expectancy_positive as check_expectancy_positive,
    welch_mean_test,
)


def _trade(
    pnl: float,
    index: int,
    *,
    tag: str = "edge",
    known_time: bool = True,
    quantity: float = 1.0,
    entry_price: float = 100.0,
    exit_price: float = 101.0,
    side: Side = Side.LONG,
) -> Trade:
    entry = datetime(2024, 1, 1) + timedelta(days=index)
    return Trade(
        symbol="TEST",
        market=Market.US_STOCK,
        side=side,
        entry_time=entry,
        exit_time=entry + timedelta(hours=1),
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        pnl=pnl,
        tag=tag,
        pnl_currency="USD",
        entry_time_known=known_time,
        exit_time_known=known_time,
    )


# JSON union keys / BOM -------------------------------------------------------


def test_json_uses_ordered_union_keys_and_later_explicit_pnl(tmp_path):
    path = tmp_path / "later-fields.json"
    rows = [
        {
            "symbol": "AAPL",
            "side": "long",
            "entry_price": 100,
            "exit_price": 110,
            "quantity": 1,
        },
        {
            "symbol": "AAPL",
            "side": "long",
            "entry_price": 100,
            "exit_price": 110,
            "quantity": 1,
            "pnl": "(USD 500)",
            "fees": 7,
            "pnl_currency": "USD",
            "entry_time": "2024-02-01T09:00:00",
            "exit_time": "2024-02-01T10:00:00",
            "tag": "later-only",
        },
    ]
    path.write_text("\ufeff" + json.dumps(rows), encoding="utf-8")

    columns, raw_rows = _rows_from_json(path)
    assert raw_rows == rows
    assert columns[:5] == [
        "symbol", "side", "entry_price", "exit_price", "quantity"
    ]
    assert columns[5:] == [
        "pnl", "fees", "pnl_currency", "entry_time", "exit_time", "tag"
    ]

    log = load_trades(path, auto_estimate_costs=False)
    assert [trade.pnl for trade in log] == [10.0, -500.0]
    assert log.trades[1].fees == 7.0
    assert log.trades[1].pnl_currency == "USD"
    assert log.trades[1].entry_time_known is True
    assert log.trades[1].exit_time_known is True
    assert log.trades[1].tag == "later-only"


def test_json_rejects_later_row_mixing_pnl_aliases(tmp_path):
    path = tmp_path / "mixed-later-pnl.json"
    common = {
        "symbol": "AAPL",
        "side": "long",
        "entry_price": 100,
        "exit_price": 102,
        "quantity": 100,
        "pnl_currency": "USD",
    }
    path.write_text(
        json.dumps([
            {**common, "net_pnl": -1000},
            {**common, "pnl": -1000},
        ]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="pnl|field_overrides|--field"):
        load_trades(path, auto_estimate_costs=False)


def test_json_rejects_conflicting_same_row_pnl_aliases(tmp_path):
    path = tmp_path / "mixed-same-row-pnl.json"
    path.write_text(
        json.dumps([{
            "symbol": "AAPL",
            "side": "long",
            "entry_price": 100,
            "exit_price": 102,
            "quantity": 100,
            "pnl_currency": "USD",
            "net_pnl": -1000,
            "pnl": 200,
        }]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="pnl|gross/net|--field"):
        load_trades(path, auto_estimate_costs=False)


def test_explicit_pnl_override_selects_canonical_alias(tmp_path):
    path = tmp_path / "explicit-pnl.json"
    path.write_text(
        json.dumps([
            {
                "symbol": "AAPL",
                "side": "long",
                "entry_price": 100,
                "exit_price": 102,
                "quantity": 100,
                "pnl_currency": "USD",
                "net_pnl": -1000,
                "pnl": 200,
            },
            {
                "symbol": "AAPL",
                "side": "long",
                "entry_price": 100,
                "exit_price": 102,
                "quantity": 100,
                "pnl_currency": "USD",
                "net_pnl": -900,
                "pnl": 300,
            },
        ]),
        encoding="utf-8",
    )

    log = load_trades(
        path,
        auto_estimate_costs=False,
        field_overrides={"pnl": "net_pnl"},
    )
    assert [trade.pnl for trade in log] == [-1000.0, -900.0]


@pytest.mark.parametrize("bad_row", [1, "trade", None, ["AAPL"]])
def test_json_rejects_every_non_object_row(tmp_path, bad_row):
    path = tmp_path / "malformed.json"
    path.write_text(
        json.dumps([{"symbol": "AAPL", "pnl": 1}, bad_row]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="第 2 筆交易必須是物件"):
        _rows_from_json(path)


# Financial number parsing ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("USDT 1,234.5", 1234.5),
        ("1,234.5 USDC", 1234.5),
        ("USD -100", -100.0),
        ("(5,000)", -5000.0),
        ("(USD 100)", -100.0),
        ("USD (100)", -100.0),
        ("(100 USDT)", -100.0),
        ("NT$ 1,000", 1000.0),
    ],
)
def test_to_float_currency_and_accounting_negatives(raw, expected):
    assert _to_float(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "(-100)",
        "USD (-100)",
        "(+100)",
        "(100",
        "100)",
        "((100))",
        "USD USDT 100",
        "USDTgarbage100",
        "100 dollars",
        True,
    ],
)
def test_to_float_rejects_ambiguous_or_garbage_values(raw):
    assert _to_float(raw, None) is None


# Integrity metadata / duplicate hold ---------------------------------------


def _write_incomplete_positive_csv(path) -> None:
    lines = ["symbol,exit_time,pnl,pnl_currency,tag"]
    for index in range(30):
        day = datetime(2024, 1, 1) + timedelta(days=index)
        lines.append(f"AAPL,{day:%Y-%m-%d},100,USD,edge")
    # A losing row is present in the source but intentionally malformed. It
    # must be counted and must prevent retained-only positives becoming edge.
    lines.append("AAPL,2024-02-15,-10000oops,USD,edge")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_rejected_row_holds_verdict_oos_stage_json_and_html(tmp_path):
    path = tmp_path / "incomplete.csv"
    _write_incomplete_positive_csv(path)

    result = analyze_file(path, n_bootstrap=100)
    assert result.log.rejected_row_count == 1
    assert result.log.rejected_row_reasons
    assert result.verdict.level == VerdictLevel.INSUFFICIENT
    assert result.verdict.should_discourage is True
    assert any(flag.code == "incomplete_input" for flag in result.verdict.red_flags)
    assert result.out_of_sample.available is False
    assert result.out_of_sample.edge_persisted is False

    payload = result.as_dict()
    assert payload["integrity"]["status"] == "incomplete"
    assert payload["integrity"]["rejected_row_count"] == 1
    assert payload["stage"]["code"] != "tiny_live_validation"
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "拒絕" in encoded

    html = render_html_report(result)
    card = render_share_card(result)
    for rendered in (html, card):
        assert "資料完整性" in rendered
        assert "拒絕 1 列" in rendered
        assert "不可認證優勢" in rendered or "不得用來認證優勢" in rendered


def test_integrity_hold_survives_filter_tag_and_sort(tmp_path):
    path = tmp_path / "incomplete.csv"
    _write_incomplete_positive_csv(path)
    log = load_trades(path)

    derived = (
        log.filter(lambda _trade: True, "all"),
        log.filter_by_tag("edge"),
        log.sorted_by_time(),
    )
    for item in derived:
        assert item.rejected_row_count == 1
        assert item.rejected_row_reasons == log.rejected_row_reasons
        assert item.integrity_complete is False


def test_direct_api_exact_duplicates_are_flagged_not_deleted():
    originals = [_trade(1.0, 0), _trade(2.0, 1), _trade(-1.0, 2)]
    log = TradeLog(originals * 10, source="direct")
    assert len(log.trades) == 30
    assert log.suspected_duplicate_count == 27

    verdict = judge(log, n_bootstrap=100)
    assert verdict.level == VerdictLevel.INSUFFICIENT
    assert verdict.should_discourage is True
    assert any(
        flag.code == "suspected_exact_duplicates" for flag in verdict.red_flags
    )
    oos = holdout_validate(log, n_bootstrap=100)
    assert oos.available is False
    assert oos.edge_persisted is False

    for derived in (log.filter(lambda t: t.pnl > 0), log.sorted_by_time()):
        assert derived.suspected_duplicate_count == 27


def test_mutable_trade_log_append_extend_keeps_duplicate_hold_after_filter():
    log = TradeLog([_trade(10.0, 0)])
    log.trades.append(_trade(10.0, 0))
    log.trades.extend([_trade(10.0, 0), _trade(-1.0, 1)])

    verdict = judge(log, n_bootstrap=100)
    assert log.suspected_duplicate_count == 2
    assert verdict.level == VerdictLevel.INSUFFICIENT
    assert any(
        flag.code == "suspected_exact_duplicates" for flag in verdict.red_flags
    )

    filtered = log.filter(lambda trade: trade.pnl > 0, "wins")
    assert len(filtered) == 3
    assert filtered.suspected_duplicate_count == 2
    assert filtered.integrity_complete is False
    oos = holdout_validate(filtered, n_bootstrap=100)
    assert oos.available is False
    assert oos.edge_persisted is False


def test_unknown_timing_and_distinct_full_tuples_do_not_prove_duplicates():
    unknown = TradeLog([_trade(5.0, 0, known_time=False) for _ in range(30)])
    assert unknown.suspected_duplicate_count == 0

    same_time = [
        _trade(5.0, 0, quantity=1, tag="a"),
        _trade(5.0, 0, quantity=2, tag="a"),
        _trade(5.0, 0, quantity=1, tag="b"),
        _trade(5.0, 0, quantity=1, tag="a", side=Side.SHORT),
        _trade(5.0, 0, quantity=1, tag="a", entry_price=99),
    ]
    assert TradeLog(same_time).suspected_duplicate_count == 0


def test_unique_positive_direct_records_remain_eligible_for_normal_judgment():
    pnls = ([10.0] * 9 + [-1.0]) * 5
    log = TradeLog([_trade(pnl, index) for index, pnl in enumerate(pnls)])
    assert log.integrity_complete is True
    assert log.suspected_duplicate_count == 0
    assert judge(log, n_bootstrap=200).level == VerdictLevel.STATISTICAL_EDGE


# Public numerical APIs / Trade direct API ----------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True])
def test_numerical_apis_reject_nonfinite_and_bool_in_every_group(bad):
    with pytest.raises(ValueError):
        check_expectancy_positive([1.0, bad], n_bootstrap=10)
    with pytest.raises(ValueError):
        welch_mean_test([1.0, bad], [2.0, 3.0])
    with pytest.raises(ValueError):
        welch_mean_test([1.0, 2.0], [3.0, bad])
    with pytest.raises(ValueError):
        required_sample_size_from_pnls([1.0, bad])


@pytest.mark.parametrize("alpha", [0, 1, -0.1, 1.1, float("nan"), True])
def test_numerical_apis_validate_alpha_before_short_sample_exit(alpha):
    with pytest.raises(ValueError):
        check_expectancy_positive([], n_bootstrap=10, alpha=alpha)
    with pytest.raises(ValueError):
        welch_mean_test([], [], alpha=alpha)
    with pytest.raises(ValueError):
        required_sample_size_from_pnls([], alpha=alpha)


@pytest.mark.parametrize("count", [0, -1, 1.5, True])
def test_bootstrap_count_must_be_positive_integer(count):
    with pytest.raises(ValueError):
        check_expectancy_positive([], n_bootstrap=count)


def test_numerical_apis_reject_huge_finite_overflow():
    huge = [1e308, 1e308]
    with pytest.raises(ValueError):
        check_expectancy_positive(huge, n_bootstrap=10)
    with pytest.raises(ValueError):
        welch_mean_test(huge, [1.0, 2.0])
    with pytest.raises(ValueError):
        required_sample_size_from_pnls(huge)


def test_empty_finite_numerical_inputs_remain_supported():
    result = check_expectancy_positive([], n_bootstrap=10)
    assert result.n == 0 and result.is_significant is False
    assert welch_mean_test([], []) is None
    assert required_sample_size_from_pnls([]) is None


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("entry_price", float("nan")),
        ("exit_price", float("inf")),
        ("quantity", float("-inf")),
        ("fees", True),
        ("contract_multiplier", "1"),
        ("pnl", float("nan")),
    ],
)
def test_trade_direct_api_rejects_invalid_financial_numbers(field, bad):
    kwargs = {
        "symbol": "AAPL",
        "market": Market.US_STOCK,
        "side": Side.LONG,
        "entry_time": datetime(2024, 1, 1),
        "exit_time": datetime(2024, 1, 2),
        "entry_price": 100.0,
        "exit_price": 101.0,
        "quantity": 1.0,
        "fees": 0.0,
        "contract_multiplier": 1.0,
        "pnl": 1.0,
    }
    kwargs[field] = bad
    with pytest.raises(ValueError):
        Trade(**kwargs)


def test_trade_computed_pnl_overflow_rejected_but_zero_placeholders_compatible():
    with pytest.raises(ValueError, match="pnl"):
        Trade(
            "AAPL",
            Market.US_STOCK,
            Side.LONG,
            datetime(2024, 1, 1),
            datetime(2024, 1, 2),
            -1e308,
            1e308,
            2.0,
            pnl=None,
        )

    placeholder = Trade(
        "AAPL",
        Market.US_STOCK,
        Side.LONG,
        datetime(2024, 1, 1),
        datetime(2024, 1, 2),
        0.0,
        0.0,
        0.0,
        pnl=5.0,
        notional_reliable=False,
    )
    assert placeholder.pnl == 5.0

    # Direct API historically permits finite negative placeholders; loader
    # decides which source rows are admissible. Do not invent a new sign rule.
    legacy = Trade(
        "AAPL",
        Market.US_STOCK,
        Side.LONG,
        datetime(2024, 1, 1),
        datetime(2024, 1, 2),
        -1.0,
        -2.0,
        -3.0,
        pnl=4.0,
    )
    assert legacy.pnl == 4.0


# Guru break-even ------------------------------------------------------------


def test_guru_exact_and_near_zero_are_break_even_not_negative():
    for win_rate in (0.5, 0.5 + 1e-15):
        result = analyze_guru_claim(
            claimed_win_rate=win_rate,
            claimed_trades=100,
            payoff_ratio=1.0,
        )
        assert result.internal_contradiction is None
        assert result.required_trades is None
        assert any("損益兩平" in finding for finding in result.findings)
        assert all("(負的)" not in finding for finding in result.findings)


def test_guru_negative_and_positive_expectancy_boundaries():
    negative = analyze_guru_claim(
        claimed_win_rate=0.49, claimed_trades=100, payoff_ratio=1.0
    )
    assert negative.internal_contradiction is not None
    assert negative.required_trades is None
    assert negative.verdict == "宣稱自相矛盾"

    positive = analyze_guru_claim(
        claimed_win_rate=0.51, claimed_trades=100, payoff_ratio=1.0
    )
    assert positive.internal_contradiction is None
    assert positive.required_trades is not None
    assert positive.required_trades != 9999
    assert all("證明「這不是運氣」" not in item for item in positive.findings)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"claimed_win_rate": float("nan")},
        {"claimed_monthly_return": float("inf")},
        {"payoff_ratio": float("-inf")},
        {"claimed_trades": True},
        {"claimed_winning_months": 1.5},
        {"total_months": -1},
        {"n_gurus_in_market": 0},
        {"null_win_prob": float("nan")},
    ],
)
def test_guru_rejects_nonfinite_or_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        analyze_guru_claim(**kwargs)
