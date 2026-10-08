"""Evidence-chart payloads shared by the browser and desktop UI.

The values in this module are descriptive facts about accepted, closed trades.
They never change the core verdict/stage and never grant live-trading access.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any, Mapping, Sequence

from core.trend.timeline import equity_curve

_SCHEMA = "ui-visuals-v1"
_QMETHOD = "linear-r7"
_MAX_N = 2000
_MAX_BINS = 20


def _as_finite_float(value: object, label: str = "value") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-bool int/float")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} is outside the finite float range") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _finite_add(left: float, right: float, label: str) -> float:
    try:
        value = left + right
    except OverflowError as exc:
        raise ValueError(f"overflow in {label}") from exc
    if not math.isfinite(value):
        raise ValueError(f"nonfinite arithmetic in {label}")
    return value


def quantile_r7(sorted_values: Sequence[float], probability: float) -> float:
    """Return the Hyndman-Fan R7 quantile: ``h=(n-1)*p``.

    The caller supplies sorted values. Inputs that cannot remain finite in the
    chart payload are rejected rather than silently converted to null or zero.
    """

    if not sorted_values:
        raise ValueError("sorted_values must be nonempty")
    p = _as_finite_float(probability, "probability")
    if p < 0.0 or p > 1.0:
        raise ValueError("probability must be in [0, 1]")
    values = [
        _as_finite_float(value, "sorted_values item") for value in sorted_values
    ]
    h = (len(values) - 1) * p
    lo_index = int(math.floor(h))
    hi_index = int(math.ceil(h))
    if lo_index == hi_index:
        return values[lo_index]
    fraction = h - lo_index
    # A convex combination avoids overflowing ``hi - lo`` for values such as
    # [-1e308, 1e308].
    try:
        result = math.fsum(
            (values[lo_index] * (1.0 - fraction), values[hi_index] * fraction)
        )
    except (OverflowError, ValueError) as exc:
        raise ValueError("overflow in quantile_r7 interpolation") from exc
    if not math.isfinite(result):
        raise ValueError("nonfinite arithmetic in quantile_r7 interpolation")
    return result


def summarize_pnls(values: Sequence[float]) -> dict[str, Any]:
    """Return finite, per-trade net-PNL descriptive statistics."""

    if not values:
        raise ValueError("values must be nonempty")
    if len(values) > _MAX_N:
        raise ValueError(f"values length exceeds {_MAX_N}")
    numbers = [_as_finite_float(value, "PNL") for value in values]
    ordered = sorted(numbers)
    try:
        mean_value = float(statistics.fmean(numbers))
    except (OverflowError, ValueError, statistics.StatisticsError) as exc:
        raise ValueError("nonfinite or overflowing mean") from exc
    if not math.isfinite(mean_value):
        raise ValueError("nonfinite or overflowing mean")
    return {
        "count": len(numbers),
        "minimum": ordered[0],
        "p05": quantile_r7(ordered, 0.05),
        "q1": quantile_r7(ordered, 0.25),
        "median": quantile_r7(ordered, 0.5),
        "q3": quantile_r7(ordered, 0.75),
        "p95": quantile_r7(ordered, 0.95),
        "maximum": ordered[-1],
        "mean": mean_value,
        "quantile_method": _QMETHOD,
    }


def _interpolate_edge(lower: float, upper: float, fraction: float) -> float:
    """Finite convex interpolation, including opposite-sign float extremes."""

    if fraction <= 0.0:
        return lower
    if fraction >= 1.0:
        return upper
    if lower < 0.0 < upper:
        try:
            value = math.fsum((lower * (1.0 - fraction), upper * fraction))
        except (OverflowError, ValueError) as exc:
            raise ValueError("overflow in histogram edge") from exc
    else:
        value = lower + (upper - lower) * fraction
    if not math.isfinite(value):
        raise ValueError("nonfinite histogram edge")
    return value


def _histogram(values: Sequence[float]) -> list[dict[str, float | int]]:
    numbers = [_as_finite_float(value, "histogram PNL") for value in values]
    if not numbers:
        raise ValueError("histogram values must be nonempty")
    minimum = min(numbers)
    maximum = max(numbers)
    if minimum == maximum:
        return [{"lower": minimum, "upper": maximum, "count": len(numbers)}]

    bin_count = min(_MAX_BINS, len(numbers))
    # Scale before subtracting so [-1e308, 1e308] never creates an infinite
    # span. The normalized range is bounded by two.
    scale = max(abs(minimum), abs(maximum), 1.0)
    scaled_minimum = minimum / scale
    scaled_maximum = maximum / scale
    scaled_span = scaled_maximum - scaled_minimum
    if not math.isfinite(scaled_span) or scaled_span <= 0.0:
        raise ValueError("invalid histogram span")
    counts = [0] * bin_count
    for value in numbers:
        if value >= maximum:
            index = bin_count - 1
        else:
            position = ((value / scale) - scaled_minimum) / scaled_span
            index = int(position * bin_count)
            index = min(bin_count - 1, max(0, index))
        counts[index] += 1

    bins: list[dict[str, float | int]] = []
    for index, count in enumerate(counts):
        lower = _interpolate_edge(minimum, maximum, index / bin_count)
        upper = _interpolate_edge(minimum, maximum, (index + 1) / bin_count)
        bins.append({"lower": lower, "upper": upper, "count": count})
    if sum(counts) != len(numbers):
        raise ValueError("histogram count mismatch")
    return bins


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _trade_pnls(trades: Sequence[Any]) -> list[float]:
    return [
        _as_finite_float(getattr(trade, "pnl", None), "trade pnl")
        for trade in trades
    ]


def _cum_curve(
    pnls: Sequence[float], times: Sequence[datetime | None]
) -> list[dict[str, Any]]:
    first_time = _iso(times[0]) if times else None
    points: list[dict[str, Any]] = [
        {
            "index": 0,
            "exit_time": first_time,
            "pnl": 0.0,
            "cum_pnl": 0.0,
            "drawdown": 0.0,
        }
    ]
    cumulative = 0.0
    peak = 0.0
    for index, raw_pnl in enumerate(pnls, start=1):
        pnl = _as_finite_float(raw_pnl, "curve pnl")
        cumulative = _finite_add(cumulative, pnl, "curve cumulative PNL")
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        if not math.isfinite(drawdown):
            raise ValueError("nonfinite arithmetic in curve drawdown")
        points.append(
            {
                "index": index,
                "exit_time": _iso(times[index - 1]) if index <= len(times) else None,
                "pnl": pnl,
                "cum_pnl": cumulative,
                "drawdown": drawdown,
            }
        )
    return points


def _segment_times_reliable(trades: Sequence[Any]) -> bool:
    """A segment curve needs a strict, fully known chronological order."""

    previous: datetime | None = None
    for trade in trades:
        current = getattr(trade, "exit_time", None)
        if not isinstance(current, datetime):
            return False
        try:
            if previous is not None and current <= previous:
                return False
        except TypeError:
            return False
        previous = current
    return bool(trades)


def _integrity_warnings(integrity: Mapping[str, Any]) -> list[str]:
    notes: list[str] = []
    if integrity.get("complete") is False:
        notes.append("交易紀錄完整性未通過；圖表只描述保留列。")
    rejected = int(integrity.get("rejected_row_count", 0) or 0)
    duplicates = int(integrity.get("suspected_duplicate_count", 0) or 0)
    if rejected:
        notes.append(f"有 {rejected} 列被拒絕載入，可能造成選擇偏差。")
    if duplicates:
        notes.append(f"有 {duplicates} 列疑似精確重複，不作優勢或風險認證。")
    return notes


def _segment_payload(
    core_segment: Mapping[str, Any],
    trades: Sequence[Any],
    *,
    currency_ok: bool,
    currency_reason: str,
) -> dict[str, Any]:
    # Preserve the core segment fields verbatim. Added amount statistics are
    # separately availability-gated and are what the charts consume.
    output = dict(core_segment)
    output["count"] = len(trades)
    output["start_time"] = _iso(getattr(trades[0], "exit_time", None)) if trades else None
    output["end_time"] = _iso(getattr(trades[-1], "exit_time", None)) if trades else None
    output["amounts_available"] = bool(currency_ok and trades)
    output["amounts_reason"] = None if output["amounts_available"] else (
        currency_reason or "沒有可比較的同幣別交易"
    )
    output["distribution"] = None
    output["mean"] = None
    output["median"] = None
    output["curve"] = []
    output["curve_available"] = False
    output["curve_reason"] = None
    if not output["amounts_available"]:
        output["curve_reason"] = output["amounts_reason"]
        return output
    pnls = _trade_pnls(trades)
    summary = summarize_pnls(pnls)
    output["distribution"] = summary
    output["mean"] = summary["mean"]
    output["median"] = summary["median"]
    if not _segment_times_reliable(trades):
        output["curve_reason"] = "此分段的出場時間不足以建立可靠曲線"
        return output
    output["curve_available"] = True
    output["curve"] = _cum_curve(
        pnls, [getattr(trade, "exit_time", None) for trade in trades]
    )
    return output


def _build_distribution(
    trades: Sequence[Any],
    *,
    currency_ok: bool,
    currency_reason: str,
    integrity_complete: bool,
) -> dict[str, Any]:
    if not currency_ok:
        return {
            "available": False,
            "reason": currency_reason or "損益幣別未確認",
            "summary": None,
            "histogram": [],
            "integrity_complete": integrity_complete,
        }
    if not trades:
        return {
            "available": False,
            "reason": "沒有可繪製的已平倉交易",
            "summary": None,
            "histogram": [],
            "integrity_complete": integrity_complete,
        }
    if len(trades) > _MAX_N:
        return {
            "available": False,
            "reason": f"交易筆數超過 {_MAX_N} 筆圖表上限",
            "summary": None,
            "histogram": [],
            "integrity_complete": integrity_complete,
        }
    try:
        pnls = _trade_pnls(trades)
        summary = summarize_pnls(pnls)
        histogram = _histogram(pnls)
    except ValueError as exc:
        return {
            "available": False,
            "reason": str(exc),
            "summary": None,
            "histogram": [],
            "integrity_complete": integrity_complete,
        }
    return {
        "available": True,
        "reason": None,
        "summary": summary,
        "histogram": histogram,
        "integrity_complete": integrity_complete,
        "label": (
            "每筆已實現淨損益（完整紀錄）"
            if integrity_complete
            else "每筆已實現淨損益（僅保留列，完整性未通過）"
        ),
    }


def _build_historical(
    result: Any,
    *,
    currency_ok: bool,
    currency_reason: str,
    sequence_ok: bool,
    sequence_reason: str,
) -> dict[str, Any]:
    unavailable = {
        "available": False,
        "reason": None,
        "points": [],
        "max_drawdown_amount": None,
        "holdout_cut_index": None,
        "basis": "realized_closed_trade_pnl",
    }
    if not currency_ok:
        return {**unavailable, "reason": currency_reason or "損益幣別未確認"}
    if not sequence_ok:
        return {**unavailable, "reason": sequence_reason or "交易時序不可靠"}
    try:
        raw_points = list(equity_curve(result.log))
        first_time = raw_points[0].exit_time if raw_points else None
        points: list[dict[str, Any]] = [
            {
                "index": 0,
                "exit_time": first_time,
                "pnl": 0.0,
                "cum_pnl": 0.0,
                "drawdown": 0.0,
            }
        ]
        for raw in raw_points:
            item = raw.as_dict()
            item["pnl"] = _as_finite_float(item.get("pnl"), "equity_curve.pnl")
            item["cum_pnl"] = _as_finite_float(
                item.get("cum_pnl"), "equity_curve.cum_pnl"
            )
            item["drawdown"] = _as_finite_float(
                item.get("drawdown"), "equity_curve.drawdown"
            )
            if item["drawdown"] < 0.0:
                raise ValueError("equity_curve.drawdown must be nonnegative")
            points.append(item)
        maximum_drawdown = max(
            (float(item["drawdown"]) for item in points), default=0.0
        )
    except (OverflowError, ValueError) as exc:
        return {**unavailable, "reason": str(exc)}
    return {
        "available": True,
        "reason": None,
        "points": points,
        "max_drawdown_amount": maximum_drawdown,
        "holdout_cut_index": None,
        "basis": "realized_closed_trade_pnl",
        "capital_translation_note": (
            "若另提供起始資金，換算仍只代表已平倉損益；"
            "不含未平倉浮動損益、入金或出金。"
        ),
    }


def _build_holdout(result: Any, *, currency_ok: bool, currency_reason: str) -> dict[str, Any]:
    report = result.out_of_sample
    core = report.as_dict()
    base = {
        "available": False,
        "reason": report.unavailable_reason or None,
        "split_index": None,
        "in_sample": None,
        "out_sample": None,
        "headline": core.get("headline"),
        "interpretation": core.get("interpretation"),
        "single_temporal_split": True,
        "untouched_during_design_verified": False,
    }
    if not report.available:
        return base
    try:
        ordered = list(result.log.sorted_by_time())
    except (TypeError, ValueError) as exc:
        return {**base, "reason": str(exc)}
    split_index = int(report.in_sample.n_trades)
    out_count = int(report.out_sample.n_trades)
    total = len(ordered)
    if (
        split_index <= 0
        or out_count <= 0
        or split_index + out_count != total
        or split_index >= total
    ):
        return {
            **base,
            "reason": (
                "核心前後段筆數與排序後紀錄不一致："
                f"{split_index}+{out_count}!={total}"
            ),
        }
    try:
        front = _segment_payload(
            core["in_sample"],
            ordered[:split_index],
            currency_ok=currency_ok,
            currency_reason=currency_reason,
        )
        back = _segment_payload(
            core["out_sample"],
            ordered[split_index:],
            currency_ok=currency_ok,
            currency_reason=currency_reason,
        )
    except (OverflowError, ValueError) as exc:
        return {**base, "reason": f"前後段圖表數值不可安全計算：{exc}"}
    relative_change: float | None = None
    relative_reason: str | None = None
    if front.get("amounts_available") and back.get("amounts_available"):
        front_mean = float(front["mean"])
        back_mean = float(back["mean"])
        if front_mean > 0.0:
            relative_change = (back_mean - front_mean) / abs(front_mean)
            if not math.isfinite(relative_change):
                relative_change = None
                relative_reason = "前後段相對變化發生非有限運算"
        else:
            relative_reason = "前段平均損益不大於 0，不提供相對衰退百分比"
    else:
        relative_reason = currency_reason or "同幣別金額統計不可用"
    return {
        **base,
        "available": True,
        "reason": None,
        "split_index": split_index,
        "in_sample": front,
        "out_sample": back,
        "relative_mean_change": relative_change,
        "relative_mean_change_reason": relative_reason,
    }


def build_visuals(result: Any) -> dict[str, Any]:
    """Build bounded chart payloads without changing the analysis decision."""

    metrics = result.metrics
    currency = str(getattr(metrics, "pnl_currency", "") or "").strip().upper()
    currency_ok = bool(getattr(metrics, "currency_reliable", False) and currency)
    currency_reason = str(getattr(metrics, "currency_note", "") or "").strip()
    if not currency_ok and not currency_reason:
        currency_reason = "損益幣別未由紀錄明確確認"
    sequence_ok = bool(getattr(metrics, "sequence_metrics_reliable", False))
    sequence_reason = str(getattr(metrics, "sequence_note", "") or "").strip()
    if not sequence_ok and not sequence_reason:
        sequence_reason = "交易出場時間不足以建立可靠順序"

    log = result.log
    integrity = log.integrity_as_dict()
    integrity_complete = bool(integrity.get("complete"))
    trades = list(log)
    distribution = _build_distribution(
        trades,
        currency_ok=currency_ok,
        currency_reason=currency_reason,
        integrity_complete=integrity_complete,
    )
    historical = _build_historical(
        result,
        currency_ok=currency_ok,
        currency_reason=currency_reason,
        sequence_ok=sequence_ok,
        sequence_reason=sequence_reason,
    )
    holdout = _build_holdout(
        result, currency_ok=currency_ok, currency_reason=currency_reason
    )
    if historical.get("available") and holdout.get("available"):
        historical["holdout_cut_index"] = holdout["split_index"]

    warnings = _integrity_warnings(integrity)
    warnings.extend(
        [
            "前後段是已完成交易的一次時序切分，不是用市場行情重新執行策略的回測。",
            "同一時間的交易不是獨立樣本；核心切分只在可用時間群組邊界進行。",
            "前後段切分無法證明策略設計時沒有看過後段資料。",
        ]
    )

    readiness = [
        {
            "key": "research_evidence",
            "label": "現有研究證據",
            "status": "available",
            "detail": "核心階段、完整性、指標與單一時序切分均照原結果呈現。",
        },
        {
            "key": "paper_scaffold",
            "label": "紙上專案鷹架",
            "status": "available",
            "detail": "可建立紙上交易專案，策略規則仍需填寫",
        },
        {
            "key": "strategy_rules",
            "label": "可執行策略規則",
            "status": "unverified",
            "detail": "交易紀錄不能重建或認證完整進出場規則。",
        },
        {
            "key": "live_execution",
            "label": "即時資料與真實執行",
            "status": "not_provided",
            "detail": "未提供、未驗證；圖表與情境不會解鎖實盤。",
        },
    ]

    eligible = integrity_complete and currency_ok and len(trades) >= 10
    return {
        "schema_version": _SCHEMA,
        "source": str(getattr(log, "source", "") or ""),
        "currency": currency or None,
        "descriptive_only": True,
        "integrity": integrity,
        "distribution": distribution,
        "historical": historical,
        "holdout": holdout,
        "automation_readiness": readiness,
        "warnings": warnings,
        "risk_simulation_available": eligible,
        "risk_simulation_reason": (
            None
            if eligible
            else "資金情境需要完整紀錄、明確單一幣別與至少 10 筆已平倉交易。"
        ),
        "can_live": False,
    }


__all__ = ["build_visuals", "quantile_r7", "summarize_pnls"]
