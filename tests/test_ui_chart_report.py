from __future__ import annotations

from core.ui.chart_report import (
    _CHART_MIN_CSS_FONT_SIZE,
    _CHART_VIEWBOX_WIDTH,
    _chart_content_width,
    _chart_font_user_size_for_card_width,
    render_evidence_report,
)


def _visuals(source: str = 'client <&"source>.csv') -> dict:
    return {
        "currency": "USD",
        "source": source,
        "provenance": {
            "analysis_id": "analysis-123",
            "revision": 7,
            "origin": "import",
            "source": source,
        },
        "historical": {
            "available": True,
            "holdout_cut_index": 2,
            "max_drawdown_amount": 15.0,
            "points": [
                {"cum_pnl": 0.0, "drawdown": 0.0},
                {"cum_pnl": 10.0, "drawdown": 0.0},
                {"cum_pnl": -5.0, "drawdown": 15.0},
                {"cum_pnl": 20.0, "drawdown": 0.0},
            ],
        },
        "distribution": {
            "available": True,
            "summary": {
                "minimum": -20.0,
                "p05": -15.0,
                "q1": -5.0,
                "median": 5.0,
                "q3": 15.0,
                "p95": 25.0,
                "maximum": 30.0,
                "mean": 6.0,
            },
            "histogram": [
                {"lower": -20.0, "upper": 0.0, "count": 2},
                {"lower": 0.0, "upper": 15.0, "count": 3},
                {"lower": 15.0, "upper": 30.0, "count": 1},
            ],
        },
        "holdout": {
            "available": True,
            "headline": "後段摘要",
            "in_sample": {
                "amounts_available": True,
                "count": 2,
                "mean": 8.0,
                "median": 7.0,
                "win_rate": 0.5,
            },
            "out_sample": {
                "amounts_available": True,
                "count": 2,
                "mean": -3.0,
                "median": -4.0,
                "win_rate": 0.25,
            },
        },
        "automation_readiness": [
            {
                "label": "紙上鷹架",
                "status": "available",
                "detail": "可建立 PaperBroker 待填框架；stage 維持 None。",
            }
        ],
        "warnings": ["只描述已接受資料。"],
    }


def _simulation() -> dict:
    result = {
        "analysis_id": "analysis-123",
        "revision": 7,
        "simulation_id": "simulation-456",
        "currency": "USD",
        "start_equity": 100.0,
        "threshold_amount": 85.0,
        "future_trades": 2,
        "hit_count": 1,
        "paths": 4,
        "hit_fraction": 0.25,
        "terminal": {
            "minimum": 80.0,
            "p05": 85.0,
            "q1": 100.0,
            "median": 120.0,
            "q3": 140.0,
            "p95": 180.0,
            "maximum": 200.0,
            "mean": 125.0,
        },
        "terminal_histogram": [
            {"lower": 80.0, "upper": 120.0, "count": 1},
            {"lower": 120.0, "upper": 160.0, "count": 2},
            {"lower": 160.0, "upper": 200.0, "count": 1},
        ],
        "fan": [
            {"step": 0, "p05": 100.0, "q1": 100.0, "median": 100.0, "q3": 100.0, "p95": 100.0},
            {"step": 1, "p05": 90.0, "q1": 95.0, "median": 100.0, "q3": 105.0, "p95": 115.0},
            {"step": 2, "p05": 80.0, "q1": 88.0, "median": 95.0, "q3": 110.0, "p95": 130.0},
        ],
    }
    return {
        "job_id": "simulation-456",
        "analysis_id": "analysis-123",
        "revision": 7,
        "status": "completed",
        "result": result,
    }


def test_report_renders_six_bound_svg_chart_types_and_escaped_metadata():
    html = render_evidence_report(_visuals(), _simulation())

    chart_types = [
        'data-chart="cumulative-pnl"',
        'data-chart="drawdown"',
        'data-chart="distribution"',
        'data-chart="holdout"',
        'data-chart="mc-fan"',
        'data-chart="terminal-histogram"',
    ]
    assert html.count("<svg") == 6
    assert all(chart_type in html for chart_type in chart_types)
    assert 'data-analysis-id="analysis-123"' in html
    assert 'data-revision="7"' in html
    assert 'data-simulation-id="simulation-456"' in html
    assert 'id="risk-title"' in html
    assert "&lt;&amp;" in html
    assert "&quot;source&gt;.csv" in html
    assert "<script>" not in html
    assert "可用" in html
    assert "紙上交易介面" in html
    assert "階段尚未設定" in html
    assert "PaperBroker" not in html


def test_offline_layout_contract_is_single_column_and_legible_at_measured_widths():
    html = render_evidence_report(_visuals(), _simulation())
    style = html.split("<style>", 1)[1].split("</style>", 1)[0]

    assert ".evidence-export .evidence-grid{display:grid;grid-template-columns:minmax(0,1fr)" in style
    assert ".evidence-card .chart-pair{display:grid;grid-template-columns:minmax(0,1fr)" in style
    assert ".evidence-card{box-sizing:border-box;grid-column:1/-1;width:100%" in style
    assert "@container evidence-card (max-width:392px)" in style
    assert "and (max-width:616px)" in style
    assert "@container evidence-card (min-width:617px) and (max-width:742px)" in style
    assert all(f"font-size:{size}px" in style for size in (31, 22, 15))

    for viewport_width in (360, 480, 700, 820, 1280):
        card_width = _chart_content_width(viewport_width)
        user_font_size = _chart_font_user_size_for_card_width(card_width)
        actual_css_size = user_font_size * card_width / _CHART_VIEWBOX_WIDTH
        assert actual_css_size >= _CHART_MIN_CSS_FONT_SIZE

    assert 'class="axis-title">交易筆數</text>' in html
    assert 'class="axis-title">路徑數</text>' in html
    assert '<tspan x="' in html and '>前段</tspan>' in html and '>中位數</tspan>' in html


def test_missing_fan_does_not_create_a_default_fan_chart():
    simulation = _simulation()
    simulation["result"] = {**simulation["result"], "fan": []}

    html = render_evidence_report(_visuals(), simulation)

    assert 'data-chart="mc-fan"' not in html
    assert 'data-chart="terminal-histogram"' in html
    assert 'data-simulation-id="simulation-456"' in html


def test_absent_simulation_has_no_simulation_metadata_or_chart():
    html = render_evidence_report(_visuals())

    assert 'data-simulation-id=' not in html
    assert 'data-chart="mc-fan"' not in html
    assert 'data-chart="terminal-histogram"' not in html
    assert "本次匯出未附資金情境" in html


def test_mismatched_simulation_result_is_not_embedded_or_leaked():
    simulation = _simulation()
    simulation["analysis_id"] = "stale-analysis"

    html = render_evidence_report(_visuals(), simulation)

    assert 'data-simulation-id=' not in html
    assert 'data-chart="mc-fan"' not in html
    assert 'data-chart="terminal-histogram"' not in html
