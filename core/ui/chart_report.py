"""Standalone, dependency-free SVG evidence charts for exported HTML."""

from __future__ import annotations

import math
from html import escape
from typing import Any, Mapping, Sequence


_CHART_VIEWBOX_WIDTH = 720.0
_CHART_MIN_CSS_FONT_SIZE = 12.0
_REPORT_MAX_WIDTH = 820.0
_REPORT_OUTER_PADDING = 24.0
_CARD_PADDING = 14.0
_CARD_BORDER = 1.0
_CHART_CONTENT_INSET = 2.0 * (_CARD_PADDING + _CARD_BORDER)
_MAX_CHART_FONT_USER_SIZE = 31.0
_CHART_SMALL_CARD_MAX = 392.0
_CHART_MEDIUM_CARD_MAX = 616.0
_CHART_LARGE_CARD_MAX = 742.0


def _chart_content_width(viewport_width: float) -> float:
    """Return the SVG CSS width after the known report/card box insets."""

    viewport = max(0.0, float(viewport_width))
    outer_width = min(viewport, _REPORT_MAX_WIDTH)
    return max(
        1.0,
        outer_width - 2.0 * _REPORT_OUTER_PADDING - _CHART_CONTENT_INSET,
    )


def _chart_font_user_size_for_card_width(card_width: float) -> float:
    """Choose SVG user units that remain at least 12 CSS px after scaling."""

    width = max(1.0, float(card_width))
    required = math.ceil(
        _CHART_MIN_CSS_FONT_SIZE * _CHART_VIEWBOX_WIDTH / width
    )
    if width <= _CHART_SMALL_CARD_MAX:
        return max(_MAX_CHART_FONT_USER_SIZE, float(required))
    if width <= _CHART_MEDIUM_CARD_MAX:
        return max(22.0, float(required))
    if width <= _CHART_LARGE_CARD_MAX:
        return max(15.0, float(required))
    return max(14.0, float(required))


def _safe_axis_left(
    values: Sequence[float],
    *,
    digits: int = 2,
    minimum: float = 86.0,
    width: float = _CHART_VIEWBOX_WIDTH,
    right_margin: float = 24.0,
) -> float:
    """Reserve room for the widest negative/decimal y tick at the largest font."""

    labels = [_fmt(value, digits) for value in values]
    widest = max(
        (
            sum(
                _MAX_CHART_FONT_USER_SIZE * (0.62 if character.isascii() else 1.0)
                for character in label
            )
            for label in labels
        ),
        default=0.0,
    )
    required = 20.0 + widest + 14.0
    return min(max(minimum, required), width - right_margin - 220.0)


def _svg_legend(
    items: Sequence[tuple[str, str, bool]],
    *,
    left: float,
    plot_width: float,
    columns: int,
    top: float = 28.0,
    row_height: float = 34.0,
) -> str:
    """Render a wrapped legend whose columns are bounded by the plot width."""

    column_count = max(1, min(columns, len(items)))
    column_width = plot_width / column_count
    parts: list[str] = []
    for index, (label, color, dashed) in enumerate(items):
        row, column = divmod(index, column_count)
        x = left + column * column_width
        y = top + row * row_height
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        parts.append(
            f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{x + 22:.2f}" y2="{y:.2f}" '
            f'stroke="{_h(color)}" stroke-width="4"{dash}/>'
            f'<text x="{x + 30:.2f}" y="{y + 5:.2f}" class="legend-label">{_h(label)}</text>'
        )
    return "".join(parts)


def _h(value: object) -> str:
    return escape(str(value), quote=True)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _fmt(value: object, digits: int = 2) -> str:
    number = _number(value)
    if number is None:
        return "無法計算"
    return f"{number:,.{digits}f}"


def _pct(value: object) -> str:
    number = _number(value)
    if number is None:
        return "無法計算"
    if number == 0.0:
        return "0%"
    if number == 1.0:
        return "100%"
    text = f"{number:.1%}"
    if text == "0.0%":
        return "<0.1%"
    if text == "100.0%":
        return ">99.9%"
    return text


def _lerp(low: float, high: float, fraction: float) -> float:
    """Interpolate finite endpoints without overflowing their raw span."""

    if fraction <= 0.0:
        return low
    if fraction >= 1.0:
        return high
    scale = max(abs(low), abs(high), 1.0)
    value = scale * (
        (low / scale) + ((high / scale) - (low / scale)) * fraction
    )
    number = _number(value)
    if number is None:
        return low if fraction < 0.5 else high
    return number


def _axis_range(values: Sequence[float], *, include_zero: bool = False) -> tuple[float, float] | None:
    numbers = [value for value in values if _number(value) is not None]
    if not numbers:
        return None
    low, high = min(numbers), max(numbers)
    if include_zero:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if low == high:
        if low == 0.0:
            return -1.0, 1.0
        if low > 0.0:
            return low * 0.9, high
        return low, high * 0.9
    return low, high


def _axis_ticks(low: float, high: float, count: int = 4) -> list[float]:
    if low == high:
        return [low]
    return [_lerp(low, high, index / count) for index in range(count + 1)]


def _y_grid(
    ticks: Sequence[float],
    *,
    left: float,
    plot_right: float,
    top: float,
    plot_height: float,
    low: float,
    high: float,
    digits: int = 2,
) -> str:
    rows: list[str] = []
    for value in ticks:
        y = top + (1.0 - _scale([low, high], low, high, value)) * plot_height
        rows.append(
            f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{plot_right:.2f}" y2="{y:.2f}" '
            'class="grid-line"/>'
            f'<text x="{left - 9:.2f}" y="{y + 5:.2f}" text-anchor="end" '
            f'class="axis-label tick-label">{_h(_fmt(value, digits))}</text>'
        )
    return "".join(rows)


def _x_ticks(count: int) -> list[int]:
    if count <= 1:
        return [0]
    middle = count // 2
    return sorted(set((0, middle, count - 1)))


def _scale(values: Sequence[float], low: float, high: float, value: float) -> float:
    if high == low:
        return 0.5
    scale = max(abs(low), abs(high), 1.0)
    lo = low / scale
    hi = high / scale
    return ((value / scale) - lo) / (hi - lo)


def _xy_path(values: Sequence[float], *, width: float, height: float, low: float, high: float) -> str:
    if not values:
        return ""
    denominator = max(1, len(values) - 1)
    return " ".join(
        f"{index / denominator * width:.2f},{(1.0 - _scale(values, low, high, value)) * height:.2f}"
        for index, value in enumerate(values)
    )


def _unavailable(title: str, reason: object) -> str:
    return (
        f'<article class="evidence-card"><h3>{_h(title)}</h3>'
        f'<p class="evidence-unavailable">無法繪製：{_h(reason or "資料不足")}</p></article>'
    )


def _historical_line_svg(
    values: Sequence[float],
    *,
    chart_name: str,
    title_id: str,
    desc_id: str,
    title: str,
    description: str,
    legend: str,
    color: str,
    low: float,
    high: float,
    point_count: int,
    cut: object,
) -> str:
    width, height, top = _CHART_VIEWBOX_WIDTH, 320.0, 58.0
    ticks = _axis_ticks(low, high)
    left = _safe_axis_left(ticks)
    plot_width, plot_height = width - left - 24.0, height - top - 70.0
    plot_right = left + plot_width
    path = _xy_path(values, width=plot_width, height=plot_height, low=low, high=high)
    grid = _y_grid(
        ticks,
        left=left,
        plot_right=plot_right,
        top=top,
        plot_height=plot_height,
        low=low,
        high=high,
    )
    zero = ""
    if low <= 0.0 <= high:
        zero_y = top + (1.0 - _scale([low, high], low, high, 0.0)) * plot_height
        zero = (
            f'<line x1="{left:.2f}" y1="{zero_y:.2f}" x2="{plot_right:.2f}" '
            f'y2="{zero_y:.2f}" class="zero-line"/>'
        )
    cut_line = ""
    if isinstance(cut, int) and not isinstance(cut, bool) and 0 < cut < point_count:
        cut_x = left + (cut / max(1, point_count - 1)) * plot_width
        cut_line = (
            f'<line x1="{cut_x:.2f}" y1="{top}" x2="{cut_x:.2f}" '
            f'y2="{top + plot_height:.2f}" class="cut-line"/>'
            f'<text x="{cut_x:.2f}" y="{top - 10:.2f}" text-anchor="middle" '
            'class="axis-label data-label cut-label">核心 holdout 切點</text>'
        )
    x_labels = []
    for index in _x_ticks(point_count):
        x = left + (index / max(1, point_count - 1)) * plot_width
        anchor = "start" if index == 0 else "end" if index == point_count - 1 else "middle"
        x_labels.append(
            f'<text x="{x:.2f}" y="{top + plot_height + 28:.2f}" text-anchor="{anchor}" '
            f'class="axis-label tick-label">{index} 筆</text>'
        )
    return f"""<svg class="evidence-chart chart-{_h(chart_name)}" data-chart="{_h(chart_name)}" viewBox="0 0 720 320" role="img" aria-labelledby="{_h(title_id)} {_h(desc_id)}">
  <title id="{_h(title_id)}">{_h(title)}</title>
  <desc id="{_h(desc_id)}">{_h(description)}</desc>
  <line x1="{left:.2f}" y1="{top + plot_height:.2f}" x2="{plot_right:.2f}" y2="{top + plot_height:.2f}" class="axis-line"/>
  {grid}{zero}{cut_line}
  <polyline transform="translate({left} {top})" points="{path}" fill="none" stroke="{_h(color)}" stroke-width="3"/>
  <line x1="{left:.2f}" y1="28" x2="{left + 28:.2f}" y2="28" stroke="{_h(color)}" stroke-width="4"/>
  <text x="{left + 38:.2f}" y="33" class="legend-label">{_h(legend)}</text>
  <text x="20" y="{top + plot_height / 2:.2f}" text-anchor="middle" transform="rotate(-90 20 {top + plot_height / 2:.2f})" class="axis-title">金額</text>
  {''.join(x_labels)}
  <text x="{left + plot_width / 2:.2f}" y="{height - 10:.2f}" text-anchor="middle" class="axis-title">交易索引（筆次）</text>
</svg>"""


def _historical_svg(historical: Mapping[str, Any], currency: str) -> str:
    if not historical.get("available"):
        return _unavailable("累積已實現損益與回撤", historical.get("reason"))
    points = list(historical.get("points") or [])
    if not points or any(not isinstance(point, Mapping) for point in points):
        return _unavailable("累積已實現損益與回撤", "圖表數值不完整")
    cumulative = [_number(point.get("cum_pnl")) for point in points]
    drawdowns = [_number(point.get("drawdown")) for point in points]
    if any(value is None for value in cumulative + drawdowns):
        return _unavailable("累積已實現損益與回撤", "圖表數值不完整")
    cumulative_values = [float(value) for value in cumulative if value is not None]
    # Drawdown is shown below zero while the data table/summary retains the
    # positive amount.
    drawdown_values = [-float(value) for value in drawdowns if value is not None]
    cumulative_range = _axis_range(cumulative_values, include_zero=True)
    drawdown_range = _axis_range(drawdown_values, include_zero=True)
    if cumulative_range is None or drawdown_range is None:
        return _unavailable("累積已實現損益與回撤", "圖表數值不完整")
    cut = historical.get("holdout_cut_index")
    end_value = cumulative_values[-1]
    maximum_dd = historical.get("max_drawdown_amount")
    return f"""<article class="evidence-card evidence-wide">
<h3>累積已實現損益與回撤</h3>
<div class="chart-pair">
{_historical_line_svg(cumulative_values, chart_name='cumulative-pnl', title_id='cumulative-title', desc_id='cumulative-desc', title='逐筆累積已實現淨損益', description='青色線為逐筆累積已實現淨損益；縱軸顯示金額，橫軸顯示交易索引。', legend='累積已實現損益', color='#147d78', low=cumulative_range[0], high=cumulative_range[1], point_count=len(points), cut=cut)}
{_historical_line_svg(drawdown_values, chart_name='drawdown', title_id='drawdown-title', desc_id='drawdown-desc', title='逐筆回撤金額', description='琥珀色線將回撤以零線下方的負向金額呈現；資料摘要仍以正值保留回撤金額。', legend='回撤（負向顯示）', color='#b36a18', low=drawdown_range[0], high=drawdown_range[1], point_count=len(points), cut=cut)}
</div>
<p class="evidence-caption">期末累積 {_fmt(end_value)} {_h(currency)}；最大回撤金額 {_fmt(maximum_dd)} {_h(currency)}。兩張圖使用同一份已平倉交易序列。</p>
</article>"""


def _distribution_svg(distribution: Mapping[str, Any], currency: str) -> str:
    if not distribution.get("available"):
        return _unavailable("單筆淨損益分布與分位數", distribution.get("reason"))
    bins = list(distribution.get("histogram") or [])
    summary = distribution.get("summary") or {}
    if not bins or any(not isinstance(item, Mapping) for item in bins):
        return _unavailable("單筆淨損益分布與分位數", "沒有直方圖資料")
    lower_values = [_number(item.get("lower")) for item in bins]
    upper_values = [_number(item.get("upper")) for item in bins]
    counts = [item.get("count") for item in bins]
    if any(value is None for value in lower_values + upper_values) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts
    ) or any(
        float(lower) > float(upper)
        for lower, upper in zip(lower_values, upper_values)
        if lower is not None and upper is not None
    ):
        return _unavailable("單筆淨損益分布與分位數", "直方圖資料不完整")
    minimum = min(float(value) for value in lower_values if value is not None)
    maximum = max(float(value) for value in upper_values if value is not None)
    chart_range = _axis_range([minimum, maximum])
    if chart_range is None:
        return _unavailable("單筆淨損益分布與分位數", "直方圖範圍不完整")
    display_low, display_high = chart_range
    width, height, top = _CHART_VIEWBOX_WIDTH, 360.0, 88.0
    maximum_count = max(max(counts), 1)
    ticks = _axis_ticks(0.0, float(maximum_count))
    left = _safe_axis_left(ticks, digits=0, minimum=78.0)
    plot_width, plot_height = width - left - 24.0, 142.0
    plot_right = left + plot_width
    bars = []
    for item, lower, upper, count in zip(bins, lower_values, upper_values, counts):
        if lower is None or upper is None:
            return _unavailable("單筆淨損益分布與分位數", "直方圖資料不完整")
        bar_left = left + _scale([display_low, display_high], display_low, display_high, float(lower)) * plot_width
        bar_right = left + _scale([display_low, display_high], display_low, display_high, float(upper)) * plot_width
        bar_height = (count / maximum_count) * plot_height
        bars.append(
            f'<rect x="{bar_left + 1:.2f}" y="{top + plot_height - bar_height:.2f}" '
            f'width="{max(1.0, bar_right - bar_left - 2):.2f}" height="{bar_height:.2f}" fill="#147d78" opacity=".78"/>'
        )
    zero_x = left + _scale([display_low, display_high], display_low, display_high, 0.0) * plot_width
    zero_line = ""
    if display_low <= 0.0 <= display_high:
        zero_line = (
            f'<line x1="{zero_x:.2f}" y1="{top}" x2="{zero_x:.2f}" '
            f'y2="{top + plot_height}" class="zero-line"/>'
        )
    q_values = {
        key: _number(summary.get(key))
        for key in ("minimum", "p05", "q1", "median", "q3", "p95", "maximum")
    }
    if any(value is None for value in q_values.values()):
        return _unavailable("單筆淨損益分布與分位數", "分位數資料不完整")
    if any(
        float(value) < minimum or float(value) > maximum
        for value in q_values.values()
        if value is not None
    ):
        return _unavailable("單筆淨損益分布與分位數", "分位數超出直方圖範圍")
    qx = {
        key: left + _scale([display_low, display_high], display_low, display_high, float(value)) * plot_width
        for key, value in q_values.items()
    }
    grid = _y_grid(
        ticks,
        left=left,
        plot_right=plot_right,
        top=top,
        plot_height=plot_height,
        low=0.0,
        high=float(maximum_count),
        digits=0,
    )
    box_y = 252.0
    legend_items = (
        ("P05", "#b36a18"),
        ("Q1", "#147d78"),
        ("中位數", "#9f3a32"),
        ("Q3", "#147d78"),
        ("P95", "#b36a18"),
    )
    legend = _svg_legend(
        [(label, color, False) for label, color in legend_items],
        left=left,
        plot_width=plot_width,
        columns=3,
    )
    return f"""<article class="evidence-card">
<h3>單筆淨損益分布與分位數</h3>
<svg class="evidence-chart chart-distribution" data-chart="distribution" viewBox="0 0 720 360" role="img" aria-labelledby="dist-title dist-desc">
  <title id="dist-title">每筆已實現淨損益直方圖與 R7 分位箱圖</title>
  <desc id="dist-desc">直方圖顯示每筆損益分布，紅線是零；下方依序標示最小值、百分之五、第一四分位、中位數、第三四分位、百分之九十五與最大值。</desc>
  {grid}{''.join(bars)}{zero_line}
  <line x1="{left}" y1="{top + plot_height}" x2="{plot_right}" y2="{top + plot_height}" class="axis-line"/>
  <text x="20" y="{top + plot_height / 2:.2f}" text-anchor="middle" transform="rotate(-90 20 {top + plot_height / 2:.2f})" class="axis-title">交易筆數</text>
  {legend}
  <line x1="{qx['minimum']:.2f}" y1="{box_y}" x2="{qx['maximum']:.2f}" y2="{box_y}" stroke="#172437"/>
  <line x1="{qx['p05']:.2f}" y1="{box_y - 13}" x2="{qx['p05']:.2f}" y2="{box_y + 13}" stroke="#b36a18"/>
  <rect x="{min(qx['q1'], qx['q3']):.2f}" y="{box_y - 18}" width="{max(1.0, abs(qx['q3'] - qx['q1'])):.2f}" height="36" fill="#dbeae5" stroke="#147d78"/>
  <line x1="{qx['median']:.2f}" y1="{box_y - 21}" x2="{qx['median']:.2f}" y2="{box_y + 21}" stroke="#9f3a32" stroke-width="2"/>
  <line x1="{qx['p95']:.2f}" y1="{box_y - 13}" x2="{qx['p95']:.2f}" y2="{box_y + 13}" stroke="#b36a18"/>
  <text x="{left}" y="306" class="axis-label tick-label">{_h(_fmt(summary.get('minimum')))}</text>
  <text x="{plot_right}" y="306" text-anchor="end" class="axis-label tick-label">{_h(_fmt(summary.get('maximum')))} {_h(currency)}</text>
  <text x="{left + plot_width / 2:.2f}" y="346" text-anchor="middle" class="axis-title">單筆淨損益（PNL，{_h(currency)}）</text>
</svg>
<div class="table-scroll"><table class="evidence-table"><thead><tr><th>最小</th><th>P05</th><th>Q1</th><th>中位數</th><th>Q3</th><th>P95</th><th>最大</th><th>平均</th></tr></thead><tbody><tr>
<td>{_h(_fmt(summary.get('minimum')))}</td><td>{_h(_fmt(summary.get('p05')))}</td><td>{_h(_fmt(summary.get('q1')))}</td><td>{_h(_fmt(summary.get('median')))}</td><td>{_h(_fmt(summary.get('q3')))}</td><td>{_h(_fmt(summary.get('p95')))}</td><td>{_h(_fmt(summary.get('maximum')))}</td><td>{_h(_fmt(summary.get('mean')))} {_h(currency)}</td>
</tr></tbody></table></div>
<p class="evidence-caption">R7 線性分位數只描述這份交易紀錄，不是母體或未來報酬的保證。</p>
</article>"""


def _holdout_chart(holdout: Mapping[str, Any], currency: str) -> str:
    front = holdout.get("in_sample") or {}
    back = holdout.get("out_sample") or {}
    if not isinstance(front, Mapping) or not isinstance(back, Mapping):
        return _unavailable("前段／後段四柱比較", "前後段資料不完整")
    if front.get("amounts_available") is False or back.get("amounts_available") is False:
        return _unavailable("前段／後段四柱比較", "同幣別金額統計不可用")
    values = [
        _number(front.get("mean")),
        _number(front.get("median")),
        _number(back.get("mean")),
        _number(back.get("median")),
    ]
    if any(value is None for value in values):
        return _unavailable("前段／後段四柱比較", "平均或中位數資料不完整")
    numeric_values = [float(value) for value in values if value is not None]
    chart_range = _axis_range(numeric_values, include_zero=True)
    if chart_range is None:
        return _unavailable("前段／後段四柱比較", "圖表數值不完整")
    low, high = chart_range
    width, height, top = _CHART_VIEWBOX_WIDTH, 330.0, 64.0
    ticks = _axis_ticks(low, high)
    left = _safe_axis_left(ticks)
    plot_width, plot_height = width - left - 24.0, 175.0
    plot_right = left + plot_width
    baseline = top + (1.0 - _scale([low, high], low, high, 0.0)) * plot_height
    grid = _y_grid(
        ticks,
        left=left,
        plot_right=plot_right,
        top=top,
        plot_height=plot_height,
        low=low,
        high=high,
    )
    labels = (("前段", "平均"), ("前段", "中位數"), ("後段", "平均"), ("後段", "中位數"))
    colors = ("#147d78", "#b36a18", "#147d78", "#b36a18")
    bars: list[str] = []
    x_labels: list[str] = []
    slot = plot_width / len(values)
    for index, (value, label, color) in enumerate(zip(numeric_values, labels, colors)):
        center = left + slot * (index + 0.5)
        value_y = top + (1.0 - _scale([low, high], low, high, value)) * plot_height
        bar_y = min(baseline, value_y)
        bar_height = max(2.0, abs(value_y - baseline))
        bars.append(
            f'<rect x="{center - 34:.2f}" y="{bar_y:.2f}" width="68" height="{bar_height:.2f}" '
            f'fill="{color}" opacity=".82"/>'
        )
        x_labels.append(
            f'<text x="{center:.2f}" y="{top + plot_height + 22:.2f}" text-anchor="middle" '
            f'class="axis-label tick-label"><tspan x="{center:.2f}" dy="0">{_h(label[0])}</tspan>'
            f'<tspan x="{center:.2f}" dy="28">{_h(label[1])}</tspan></text>'
        )
    return f"""<svg class="evidence-chart chart-holdout" data-chart="holdout" viewBox="0 0 720 330" role="img" aria-labelledby="holdout-title holdout-desc">
  <title id="holdout-title">前段與後段平均及中位數四柱比較</title>
  <desc id="holdout-desc">使用既有樣本內與樣本外的平均損益及中位數，不重新抽樣；琥珀色是中位數，青色是平均值。</desc>
  {grid}
  <line x1="{left:.2f}" y1="{baseline:.2f}" x2="{plot_right:.2f}" y2="{baseline:.2f}" class="zero-line"/>
  {''.join(bars)}
  <line x1="{left}" y1="{top + plot_height}" x2="{plot_right}" y2="{top + plot_height}" class="axis-line"/>
  {_svg_legend([('平均', '#147d78', False), ('中位數', '#b36a18', False)], left=left, plot_width=plot_width, columns=2)}
  <text x="20" y="{top + plot_height / 2:.2f}" text-anchor="middle" transform="rotate(-90 20 {top + plot_height / 2:.2f})" class="axis-title">金額</text>
  {''.join(x_labels)}
  <text x="{left + plot_width / 2:.2f}" y="{height - 10:.2f}" text-anchor="middle" class="axis-title">樣本段與統計量（{_h(currency)}）</text>
</svg>"""


def _holdout_html(holdout: Mapping[str, Any], currency: str) -> str:
    if not holdout.get("available"):
        return _unavailable("前段／後段單一時序比較", holdout.get("reason"))
    front = holdout.get("in_sample") or {}
    back = holdout.get("out_sample") or {}
    if not isinstance(front, Mapping):
        front = {}
    if not isinstance(back, Mapping):
        back = {}

    def card(label: str, segment: Mapping[str, Any]) -> str:
        amount_ok = bool(segment.get("amounts_available"))
        mean = _fmt(segment.get("mean")) if amount_ok else "無法計算"
        median = _fmt(segment.get("median")) if amount_ok else "無法計算"
        win_rate = _pct(segment.get("win_rate"))
        start = str(segment.get("start_time") or "未知")[:10]
        end = str(segment.get("end_time") or "未知")[:10]
        return f"""<div class="segment-card"><b>{_h(label)}</b>
<dl><div><dt>筆數</dt><dd>{_h(segment.get('count', segment.get('n_trades', 0)))}</dd></div>
<div><dt>平均</dt><dd>{_h(mean)} {_h(currency if amount_ok else '')}</dd></div>
<div><dt>中位數</dt><dd>{_h(median)} {_h(currency if amount_ok else '')}</dd></div>
<div><dt>勝率</dt><dd>{_h(win_rate)}</dd></div>
<div><dt>日期</dt><dd>{_h(start)} ～ {_h(end)}</dd></div></dl></div>"""

    return f"""<article class="evidence-card evidence-wide">
<h3>前段／後段單一時序比較</h3>
{_holdout_chart(holdout, currency)}
<div class="segment-grid">{card('樣本內（前段）', front)}{card('樣本外（後段）', back)}</div>
<p><b>{_h(holdout.get('headline') or '')}</b></p>
<p class="evidence-caption">這是完成交易的一次 holdout 切分，不是市場價格策略回測；也無法證明後段在策略設計時完全未被看過。</p>
</article>"""


def _risk_payload(simulation: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(simulation, Mapping):
        return None
    nested = simulation.get("result")
    return nested if isinstance(nested, Mapping) else simulation


def _validated_simulation(
    simulation: Mapping[str, Any] | None,
    provenance: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str | None] | None:
    """Return only a completed result bound to the current analysis revision."""

    if not isinstance(simulation, Mapping):
        return None
    if simulation.get("status") not in (None, "completed"):
        return None
    risk = simulation.get("result")
    if not isinstance(risk, Mapping):
        return None
    expected_analysis = provenance.get("analysis_id")
    expected_revision = provenance.get("revision")
    if expected_analysis is None or expected_revision is None:
        return None
    if simulation.get("analysis_id") != expected_analysis:
        return None
    if simulation.get("revision") != expected_revision:
        return None
    for payload in (risk,):
        if "analysis_id" in payload and payload.get("analysis_id") != expected_analysis:
            return None
        if "revision" in payload and payload.get("revision") != expected_revision:
            return None
        nested_provenance = payload.get("provenance")
        if isinstance(nested_provenance, Mapping):
            if (
                "analysis_id" in nested_provenance
                and nested_provenance.get("analysis_id") != expected_analysis
            ):
                return None
            if (
                "revision" in nested_provenance
                and nested_provenance.get("revision") != expected_revision
            ):
                return None
    outer_id = simulation.get("job_id") or simulation.get("simulation_id")
    result_id = risk.get("simulation_id")
    if outer_id is not None and result_id is not None and outer_id != result_id:
        return None
    simulation_id = outer_id or result_id
    return risk, str(simulation_id) if simulation_id is not None else None


def _terminal_summary_table(terminal: Mapping[str, Any], currency: str) -> str:
    keys = ("minimum", "p05", "q1", "median", "q3", "p95", "maximum", "mean")
    labels = ("最小", "P05", "Q1", "中位數", "Q3", "P95", "最大", "平均")
    cells = "".join(
        f"<td>{_h(_fmt(terminal.get(key)))}{(' ' + _h(currency)) if index in (0, 3, 6, 7) and currency else ''}</td>"
        for index, key in enumerate(keys)
    )
    headings = "".join(f"<th>{_h(label)}</th>" for label in labels)
    return f'<div class="table-scroll"><table class="evidence-table terminal-summary"><thead><tr>{headings}</tr></thead><tbody><tr>{cells}</tr></tbody></table></div>'


def _terminal_histogram_svg(risk: Mapping[str, Any], currency: str) -> str:
    bins = list(risk.get("terminal_histogram") or [])
    terminal = risk.get("terminal") or {}
    if not bins or not isinstance(terminal, Mapping) or any(
        not isinstance(item, Mapping) for item in bins
    ):
        return _unavailable("期末資金分布", "期末資金直方圖資料不存在")
    lowers = [_number(item.get("lower")) for item in bins]
    uppers = [_number(item.get("upper")) for item in bins]
    counts = [item.get("count") for item in bins]
    if any(value is None for value in lowers + uppers) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts
    ):
        return _unavailable("期末資金分布", "期末資金直方圖資料不完整")
    if any(
        float(lower) > float(upper)
        for lower, upper in zip(lowers, uppers)
        if lower is not None and upper is not None
    ):
        return _unavailable("期末資金分布", "期末資金直方圖範圍不完整")
    minimum = min(float(value) for value in lowers if value is not None)
    maximum = max(float(value) for value in uppers if value is not None)
    chart_range = _axis_range([minimum, maximum])
    if chart_range is None:
        return _unavailable("期末資金分布", "期末資金範圍不完整")
    display_low, display_high = chart_range
    summary_keys = ("minimum", "p05", "q1", "median", "q3", "p95", "maximum", "mean")
    summary = {key: _number(terminal.get(key)) for key in summary_keys}
    if any(value is None for value in summary.values()):
        return _unavailable("期末資金分布", "期末資金摘要不完整")
    if any(
        float(value) < minimum or float(value) > maximum
        for value in summary.values()
        if value is not None
    ):
        return _unavailable("期末資金分布", "期末資金摘要超出直方圖範圍")
    width, height, top = _CHART_VIEWBOX_WIDTH, 360.0, 88.0
    maximum_count = max(max(counts), 1)
    ticks = _axis_ticks(0.0, float(maximum_count))
    left = _safe_axis_left(ticks, digits=0, minimum=78.0)
    plot_width, plot_height = width - left - 24.0, 142.0
    plot_right = left + plot_width
    bars: list[str] = []
    for lower, upper, count in zip(lowers, uppers, counts):
        if lower is None or upper is None:
            return _unavailable("期末資金分布", "期末資金直方圖資料不完整")
        bar_left = left + _scale([display_low, display_high], display_low, display_high, float(lower)) * plot_width
        bar_right = left + _scale([display_low, display_high], display_low, display_high, float(upper)) * plot_width
        bar_height = (count / maximum_count) * plot_height
        bars.append(
            f'<rect x="{bar_left + 1:.2f}" y="{top + plot_height - bar_height:.2f}" '
            f'width="{max(1.0, bar_right - bar_left - 2):.2f}" height="{bar_height:.2f}" fill="#0c5f5c" opacity=".78"/>'
        )
    grid = _y_grid(
        ticks,
        left=left,
        plot_right=plot_right,
        top=top,
        plot_height=plot_height,
        low=0.0,
        high=float(maximum_count),
        digits=0,
    )
    q_colors = {
        "p05": "#b36a18",
        "q1": "#147d78",
        "median": "#9f3a32",
        "q3": "#147d78",
        "p95": "#b36a18",
    }
    q_labels = {"p05": "P05", "q1": "Q1", "median": "中位數", "q3": "Q3", "p95": "P95"}
    legend = _svg_legend(
        [
            (q_labels[key], q_colors[key], False)
            for key in ("p05", "q1", "median", "q3", "p95")
        ],
        left=left,
        plot_width=plot_width,
        columns=3,
    )
    markers: list[str] = []
    marker_y = top + plot_height + 28.0
    for key in ("p05", "q1", "median", "q3", "p95"):
        x = left + _scale([display_low, display_high], display_low, display_high, float(summary[key])) * plot_width
        markers.append(
            f'<line x1="{x:.2f}" y1="{marker_y - 12:.2f}" x2="{x:.2f}" y2="{marker_y + 12:.2f}" stroke="{q_colors[key]}" stroke-width="2"/>'
        )
    return f"""<article class="evidence-card evidence-wide">
<h3>期末資金分布</h3>
<svg class="evidence-chart chart-terminal-histogram" data-chart="terminal-histogram" viewBox="0 0 720 360" role="img" aria-labelledby="terminal-title terminal-desc">
  <title id="terminal-title">資金情境期末資金直方圖</title>
  <desc id="terminal-desc">直方圖使用既有情境結果的期末資金區間與計數，不重新抽樣；下方標示 P05、Q1、中位數、Q3 與 P95。</desc>
  {grid}{''.join(bars)}
  <line x1="{left}" y1="{top + plot_height}" x2="{plot_right}" y2="{top + plot_height}" class="axis-line"/>
  <text x="20" y="{top + plot_height / 2:.2f}" text-anchor="middle" transform="rotate(-90 20 {top + plot_height / 2:.2f})" class="axis-title">路徑數</text>
  {legend}{''.join(markers)}
  <text x="{left}" y="306" class="axis-label tick-label">{_h(_fmt(minimum))}</text>
  <text x="{plot_right}" y="306" text-anchor="end" class="axis-label tick-label">{_h(_fmt(maximum))} {_h(currency)}</text>
  <text x="{left + plot_width / 2:.2f}" y="346" text-anchor="middle" class="axis-title">期末資金（{_h(currency)}）</text>
</svg>
{_terminal_summary_table(terminal, currency)}
<p class="evidence-caption">這張圖只讀取已完成資金情境的期末資金直方圖與摘要；情境頻率不是未來機率。</p>
</article>"""


def _risk_html_payload(risk: Mapping[str, Any] | None) -> str:
    if not risk:
        return _unavailable("資金警戒線情境", "本次匯出未附資金情境")
    fan = list(risk.get("fan") or [])
    if not fan or any(not isinstance(point, Mapping) for point in fan):
        fan_html = _unavailable("資金警戒線情境", "情境扇形資料不存在")
    else:
        fields = ("p05", "q1", "median", "q3", "p95")
        series = {key: [_number(point.get(key)) for point in fan] for key in fields}
        threshold = _number(risk.get("threshold_amount"))
        if threshold is None or any(
            any(value is None for value in values) for values in series.values()
        ):
            fan_html = _unavailable("資金警戒線情境", "情境數值不完整")
        else:
            numeric_series = {
                key: [float(value) for value in values if value is not None]
                for key, values in series.items()
            }
            all_values = [
                value for rows in numeric_series.values() for value in rows
            ] + [float(threshold)]
            chart_range = _axis_range(all_values, include_zero=True)
            if chart_range is None:
                fan_html = _unavailable("資金警戒線情境", "情境數值不完整")
            else:
                low, high = chart_range
                width, height, top = _CHART_VIEWBOX_WIDTH, 360.0, 88.0
                axis_ticks = _axis_ticks(low, high)
                left = _safe_axis_left(axis_ticks)
                plot_width, plot_height = width - left - 24.0, 178.0
                plot_right = left + plot_width

                def coords(key: str) -> list[tuple[float, float]]:
                    rows = numeric_series[key]
                    denominator = max(1, len(rows) - 1)
                    return [
                        (
                            left + index / denominator * plot_width,
                            top + (1.0 - _scale([low, high], low, high, value)) * plot_height,
                        )
                        for index, value in enumerate(rows)
                    ]

                outer = coords("p05") + list(reversed(coords("p95")))
                inner = coords("q1") + list(reversed(coords("q3")))
                polygon = lambda points: " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
                median = polygon(coords("median"))
                threshold_y = top + (1.0 - _scale([low, high], low, high, float(threshold))) * plot_height
                ticks = _y_grid(
                    axis_ticks,
                    left=left,
                    plot_right=plot_right,
                    top=top,
                    plot_height=plot_height,
                    low=low,
                    high=high,
                )
                future = _number(risk.get("future_trades"))
                if future is None:
                    future = float(max(0, len(fan) - 1))
                x_labels = []
                for index in _x_ticks(len(fan)):
                    x = left + (index / max(1, len(fan) - 1)) * plot_width
                    step = _lerp(0.0, float(future), index / max(1, len(fan) - 1))
                    anchor = "start" if index == 0 else "end" if index == len(fan) - 1 else "middle"
                    x_labels.append(
                        f'<text x="{x:.2f}" y="{top + plot_height + 28:.2f}" text-anchor="{anchor}" class="axis-label tick-label">第 {_h(_fmt(step, 0))} 筆</text>'
                    )
                legend_items = (
                    ("P05–P95", "#7db5ad", "band"),
                    ("Q1–Q3", "#0c5f5c", "band"),
                    ("中位數", "#0c5f5c", "line"),
                    ("資金門檻", "#9f3a32", "line"),
                )
                legend = _svg_legend(
                    [(label, color, key == "line" and label == "資金門檻") for label, color, key in legend_items],
                    left=left,
                    plot_width=plot_width,
                    columns=2,
                )
                currency = str(risk.get("currency") or "")
                terminal = risk.get("terminal") or {}
                if not isinstance(terminal, Mapping):
                    terminal = {}
                warnings = "".join(f"<li>{_h(item)}</li>" for item in (risk.get("warnings") or []))
                zero_note = risk.get("zero_hit_note")
                fan_html = f"""<article class="evidence-card evidence-wide">
<h3>資金警戒線情境</h3>
<svg class="evidence-chart chart-mc-fan" data-chart="mc-fan" viewBox="0 0 720 360" role="img" aria-labelledby="risk-title risk-desc">
  <title id="risk-title">固定幣別淨損益重抽樣的資金分位扇形</title>
  <desc id="risk-desc">外層為百分之五到百分之九十五，內層為第一到第三四分位，中線為中位數；紅虛線為嚴格跌破才算命中的資金警戒線。</desc>
  {ticks}
  <polygon points="{polygon(outer)}" fill="#dbeae5" opacity=".7"/>
  <polygon points="{polygon(inner)}" fill="#7db5ad" opacity=".68"/>
  <polyline points="{median}" fill="none" stroke="#0c5f5c" stroke-width="3"/>
  <line x1="{left}" y1="{threshold_y:.2f}" x2="{plot_right}" y2="{threshold_y:.2f}" stroke="#9f3a32" stroke-width="2" stroke-dasharray="5 4"/>
  <line x1="{left}" y1="{top + plot_height}" x2="{plot_right}" y2="{top + plot_height}" class="axis-line"/>
  <text x="20" y="{top + plot_height / 2:.2f}" text-anchor="middle" transform="rotate(-90 20 {top + plot_height / 2:.2f})" class="axis-title">資金</text>
  {legend}{''.join(x_labels)}
  <text x="{left + plot_width / 2:.2f}" y="{height - 10:.2f}" text-anchor="middle" class="axis-title">未來交易筆次</text>
</svg>
<div class="risk-facts"><span>起始資金 <b>{_h(_fmt(risk.get('start_equity')))} {_h(currency)}</b></span><span>資金門檻 <b>{_h(_fmt(threshold))} {_h(currency)}</b></span><span>曾跌破 <b>{_h(risk.get('hit_count'))}/{_h(risk.get('paths'))}（{_h(_pct(risk.get('hit_fraction')))}）</b></span><span>期末中位數 <b>{_h(_fmt(terminal.get('median')))} {_h(currency)}</b></span></div>
{f'<p class="evidence-unavailable">{_h(zero_note)}</p>' if zero_note else ''}
<details><summary>模型限制與停止假設</summary><ul>{warnings}</ul></details>
</article>"""
    terminal_html = _terminal_histogram_svg(risk, str(risk.get("currency") or ""))
    return fan_html + terminal_html


def _risk_html(simulation: Mapping[str, Any] | None) -> str:
    return _risk_html_payload(_risk_payload(simulation))


_READINESS_STATUS_LABELS = {
    "available": "可用",
    "unverified": "未驗證",
    "not_provided": "未提供",
    "unavailable": "不可用",
    "blocked": "受阻",
    "ready": "已準備",
    "pending": "待處理",
}


def _readiness_status(value: object) -> str:
    key = str(value or "not_provided").strip().lower()
    return _READINESS_STATUS_LABELS.get(key, "未驗證")


def _readiness_detail(value: object) -> str:
    detail = str(value or "")
    return (
        detail.replace("PaperBroker", "紙上交易介面")
        .replace("paper broker", "紙上交易介面")
        .replace("stage 維持 None", "階段尚未設定")
        .replace("stage=None", "階段尚未設定")
        .replace("stage: None", "階段尚未設定")
        .replace("None", "尚未設定")
    )


def render_evidence_report(
    visuals: Mapping[str, Any], simulation: Mapping[str, Any] | None = None
) -> str:
    """Render an offline HTML section using only supplied chart numbers."""

    currency = str(visuals.get("currency") or "")
    provenance = visuals.get("provenance") or {}
    if not isinstance(provenance, Mapping):
        provenance = {}
    analysis_id = provenance.get("analysis_id") or "unknown"
    revision = provenance.get("revision")
    if revision is None:
        revision = "unknown"
    source = provenance.get("source", visuals.get("source", ""))
    origin_labels = {
        "demo": "內建示範資料",
        "import": "使用者匯入資料",
        "manual": "使用者手動輸入資料",
        "api": "API 唯讀資料",
    }
    origin = origin_labels.get(str(provenance.get("origin") or ""), "來源未辨識")
    provenance_line = f"資料來源：{origin}"
    if source:
        provenance_line += f"（{source}）"
    bound_simulation = _validated_simulation(simulation, provenance)
    risk = bound_simulation[0] if bound_simulation is not None else None
    simulation_id = bound_simulation[1] if bound_simulation is not None else None
    metadata = (
        f'data-analysis-id="{_h(analysis_id)}" '
        f'data-revision="{_h(revision)}"'
    )
    if simulation_id is not None:
        metadata += f' data-simulation-id="{_h(simulation_id)}"'
    warnings = "".join(f"<li>{_h(item)}</li>" for item in (visuals.get("warnings") or []))
    readiness_rows = "".join(
        f'<tr><td>{_h(item.get("label", ""))}</td><td>{_h(_readiness_status(item.get("status")))}</td><td>{_h(_readiness_detail(item.get("detail", "")))}</td></tr>'
        for item in (visuals.get("automation_readiness") or [])
        if isinstance(item, Mapping)
    )
    return f"""
<style>
.evidence-export{{box-sizing:border-box;max-width:100%;margin-top:32px;padding-top:14px;border-top:2px solid #147d78;overflow-x:hidden}}
.evidence-export .evidence-grid{{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}}
.evidence-card{{box-sizing:border-box;grid-column:1/-1;width:100%;min-width:0;max-width:100%;padding:14px;background:#fbf8f0;color:#172437;border:1px solid #cbc2af;border-radius:8px;overflow:hidden;container-type:inline-size;container-name:evidence-card}}
.evidence-card h3{{margin:0 0 10px;font-size:16px}}.evidence-card svg{{display:block;width:100%;max-width:100%;height:auto;min-width:0;overflow:visible}}
.evidence-card .chart-pair{{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}}
.evidence-card .grid-line{{stroke:#d8d4c8;stroke-width:1}}.evidence-card .axis-line{{stroke:#69737d;stroke-width:1.5}}.evidence-card .zero-line{{stroke:#9f3a32;stroke-width:1.5;stroke-dasharray:5 4}}.evidence-card .cut-line{{stroke:#b36a18;stroke-width:1.5;stroke-dasharray:5 4}}
.evidence-card .axis-label,.evidence-card .legend-label,.evidence-card .axis-title{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;fill:#506071}}.evidence-card .legend-label{{fill:#172437}}
.evidence-wide{{grid-column:1/-1}}.evidence-caption,.evidence-unavailable{{color:#506071;font-size:12px;line-height:1.55}}
.table-scroll{{max-width:100%;overflow-x:auto}}.evidence-table{{width:100%;font-size:12px;border-collapse:collapse;table-layout:auto}}.evidence-table th,.evidence-table td{{padding:5px;text-align:right;border-bottom:1px solid #cbc2af;overflow-wrap:anywhere;white-space:normal}}
.segment-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.segment-card{{padding:12px;border:1px solid #cbc2af;background:#fffdf8}}
.segment-card dl{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}.segment-card dl div{{min-width:0}}.segment-card dt{{font-size:11px;color:#506071}}.segment-card dd{{margin:2px 0 0;font-weight:700;overflow-wrap:anywhere}}
.risk-facts{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}}.risk-facts span{{padding:8px;background:#fffdf8;border:1px solid #cbc2af;font-size:12px}}.risk-facts b{{display:block;margin-top:3px}}
@media(max-width:700px){{.segment-grid,.risk-facts{{grid-template-columns:1fr}}}}
/* Fallback breakpoints use the known 24px report and 14px card insets. */
@media(max-width:{_CHART_SMALL_CARD_MAX + 2.0 * _REPORT_OUTER_PADDING + _CHART_CONTENT_INSET:.0f}px){{.evidence-card svg .axis-label,.evidence-card svg .legend-label,.evidence-card svg .axis-title{{font-size:{_MAX_CHART_FONT_USER_SIZE:.0f}px}}.evidence-card h3{{font-size:18px}}.evidence-caption,.evidence-unavailable{{font-size:13px}}}}
@media(min-width:{_CHART_SMALL_CARD_MAX + 2.0 * _REPORT_OUTER_PADDING + _CHART_CONTENT_INSET + 1.0:.0f}px) and (max-width:{_CHART_MEDIUM_CARD_MAX + 2.0 * _REPORT_OUTER_PADDING + _CHART_CONTENT_INSET:.0f}px){{.evidence-card svg .axis-label,.evidence-card svg .legend-label,.evidence-card svg .axis-title{{font-size:22px}}}}
@media(min-width:{_CHART_MEDIUM_CARD_MAX + 2.0 * _REPORT_OUTER_PADDING + _CHART_CONTENT_INSET + 1.0:.0f}px){{.evidence-card svg .axis-label,.evidence-card svg .legend-label,.evidence-card svg .axis-title{{font-size:15px}}}}
@container evidence-card (max-width:{_CHART_SMALL_CARD_MAX:.0f}px){{.evidence-card svg .axis-label,.evidence-card svg .legend-label,.evidence-card svg .axis-title{{font-size:{_MAX_CHART_FONT_USER_SIZE:.0f}px}}}}
@container evidence-card (min-width:{_CHART_SMALL_CARD_MAX + 1.0:.0f}px) and (max-width:{_CHART_MEDIUM_CARD_MAX:.0f}px){{.evidence-card svg .axis-label,.evidence-card svg .legend-label,.evidence-card svg .axis-title{{font-size:22px}}}}
@container evidence-card (min-width:{_CHART_MEDIUM_CARD_MAX + 1.0:.0f}px) and (max-width:{_CHART_LARGE_CARD_MAX:.0f}px){{.evidence-card svg .axis-label,.evidence-card svg .legend-label,.evidence-card svg .axis-title{{font-size:15px}}}}
</style>
<section class="evidence-export" {metadata} aria-labelledby="evidence-export-title">
<h2 id="evidence-export-title">策略證據圖表</h2>
<p class="muted">圖表只描述已接受的已平倉交易，不會覆蓋核心階段，也不構成實盤許可。</p>
<p class="muted">{_h(provenance_line)}</p>
<div class="evidence-grid">
{_historical_svg(visuals.get('historical') or {}, currency)}
{_distribution_svg(visuals.get('distribution') or {}, currency)}
{_holdout_html(visuals.get('holdout') or {}, currency)}
{_risk_html_payload(risk)}
</div>
<h3>自動化準備事實</h3><div class="table-scroll"><table class="evidence-table readiness-table"><thead><tr><th>項目</th><th>狀態</th><th>說明</th></tr></thead><tbody>{readiness_rows}</tbody></table></div>
<details><summary>圖表解讀限制</summary><ul>{warnings}</ul></details>
</section>"""


def render_share_summary(
    visuals: Mapping[str, Any], simulation: Mapping[str, Any] | None = None
) -> str:
    """Return a concise escaped quartile/risk block for an existing share card."""

    distribution = visuals.get("distribution") or {}
    summary = distribution.get("summary") or {}
    currency = str(visuals.get("currency") or "")
    if distribution.get("available"):
        quartiles = (
            f"Q1 {_fmt(summary.get('q1'))}／中位數 {_fmt(summary.get('median'))}／"
            f"Q3 {_fmt(summary.get('q3'))} {currency}"
        )
    else:
        quartiles = f"損益分位數：無法計算（{distribution.get('reason') or '資料不足'}）"
    risk = _risk_payload(simulation)
    risk_line = "資金情境：本次未附"
    if risk:
        risk_line = (
            f"跌破資金門檻：{risk.get('hit_count')}/{risk.get('paths')} 條路徑"
            f"（{_pct(risk.get('hit_fraction'))}；情境頻率，非未來機率）"
        )
    provenance = visuals.get("provenance") or {}
    binding = (
        f"分析 {provenance.get('analysis_id', 'unknown')}／"
        f"revision {provenance.get('revision', 'unknown')}"
    )
    return (
        '<div style="margin-top:14px;padding:12px 14px;background:#0f2626;'
        'border:1px solid #285b5a;border-radius:8px;font-size:13px;line-height:1.6">'
        f"<b>每筆已實現淨損益分位數</b><br>{_h(quartiles)}<br>{_h(risk_line)}"
        f'<br><span style="font-size:10px;color:#8da3a2">{_h(binding)}</span>'
        "</div>"
    )


__all__ = ["render_evidence_report", "render_share_summary"]
