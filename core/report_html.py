"""HTML 報告 + 分享圖卡 — 把分析結果變成可存檔、可傳給家人的白紙黑字數據。

用途:勸阻長輩跟單時,甩一張數據卡片比講一百句話有用。

安全與誠實的設計:
  - 所有動態內容一律 html.escape(quote=True),防 XSS。
  - **不畫「信心度儀表」**:p 值不是「優勢為真的機率」,把 1−p 畫成
    95% 信心度會與同頁的「信賴區間涵蓋 0」直接矛盾。改為直接呈現 CI 與原始 p。
  - **逐策略不發優勢徽章**:多重比較未校正會把運氣認證成優勢(見 per_tag)。
  - **不做未來報酬投射**:純外推正是詐騙話術本體。
  - 自包含、無外部資源(不載 CDN、不連網),可離線開啟。
"""

from __future__ import annotations

from html import escape as _esc

from .metrics.performance import fmt_ratio as _fr


def h(x) -> str:
    """脈絡安全的跳脫(含引號,可放進屬性)。"""
    return _esc(str(x), quote=True)


def _equity_svg(pnls: list[float], width: int = 720, height: int = 220) -> str:
    """用 inline SVG 畫累積權益曲線(含最大回撤陰影)。零外部相依。"""
    if not pnls:
        return '<p class="muted">沒有交易資料可繪圖。</p>'

    equity, cum = [], 0.0
    for p in pnls:
        cum += p
        equity.append(cum)

    lo, hi = min(equity + [0.0]), max(equity + [0.0])
    span = (hi - lo) or 1.0
    pad = 20
    w, hgt = width - 2 * pad, height - 2 * pad

    def X(i: int) -> float:
        return pad + (i / max(1, len(equity) - 1)) * w

    def Y(v: float) -> float:
        return pad + (hi - v) / span * hgt

    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(equity))

    # 最大回撤區段(峰值 → 谷底)
    peak = equity[0]
    peak_i = 0
    best = (0.0, 0, 0)
    for i, v in enumerate(equity):
        if v > peak:
            peak, peak_i = v, i
        dd = peak - v
        if dd > best[0]:
            best = (dd, peak_i, i)
    dd_shade = ""
    if best[0] > 0:
        x1, x2 = X(best[1]), X(best[2])
        dd_shade = (
            f'<rect x="{x1:.1f}" y="{pad}" width="{max(1.0, x2 - x1):.1f}" '
            f'height="{hgt}" fill="#ef5350" opacity="0.12"/>'
        )

    zero_y = Y(0.0)
    return f"""<svg viewBox="0 0 {width} {height}" width="100%" height="{height}"
  role="img" aria-label="累積損益曲線">
  {dd_shade}
  <line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}"
        stroke="#6b7280" stroke-dasharray="4 4" stroke-width="1"/>
  <polyline points="{pts}" fill="none" stroke="#2962ff" stroke-width="2"/>
</svg>
<p class="muted">紅色區塊 = 最大回撤區間;虛線 = 損益平衡點。</p>"""


def _verdict_color(level: str) -> str:
    return {
        "gambling": "#ef5350",
        "insufficient": "#f59e0b",
        "luck_suspected": "#eab308",
        "fragile_edge": "#eab308",
        "statistical_edge": "#26a69a",
    }.get(level, "#6b7280")


def _stage_colors(code: str) -> tuple[str, str, str]:
    """階段卡片的背景、邊框、文字色；停手不可和通過共用綠色。"""

    return {
        "stop_real_money": ("#241414", "#7f1d1d", "#fca5a5"),
        "paper_only": ("#241e0f", "#854d0e", "#fde68a"),
        "paper_until_oos": ("#241e0f", "#854d0e", "#fde68a"),
        "paper_until_risk_data": ("#241e0f", "#854d0e", "#fde68a"),
        "tiny_live_validation": ("#102019", "#285b43", "#9ad9b5"),
    }.get(code, ("#131722", "#374151", "#d1d4dc"))


def render_html_report(
    result,
    *,
    title: str = "反詐投資王 — 交易績效誠實報告",
    trend=None,
    scenario=None,
    scenario_note: str | None = None,
) -> str:
    """把 AnalysisResult 渲染成自包含 HTML。純 passthrough,不新增任何結論。

    trend / scenario 為 --full 健檢時的選配區塊(TrendReport / RuinScenario);
    預設 None 保持精簡 —— HTML 報告的定位是「傳給家人的白紙黑字數據」,不稀釋裁決。
    scenario_note:--full 但模擬被略過時的原因,會渲染成明確的「已略過」區塊;
    終端說了「略過」而 HTML 靜默消失 = 兩個通道誠實度不一致,禁止。
    """
    v = result.verdict
    m = result.metrics
    currency = f" {m.pnl_currency}" if m.pnl_currency else ""
    oos = result.out_of_sample
    color = _verdict_color(v.level.value)
    from .onboarding import stage_from_analysis

    stage = stage_from_analysis(result)
    stage_bg, stage_border, stage_text = _stage_colors(stage.code)

    integrity = result.log.integrity_as_dict()
    integrity_html = ""
    if not integrity["complete"]:
        issue_bits = []
        if integrity["rejected_row_count"]:
            issue_bits.append(f"拒絕 {integrity['rejected_row_count']} 列")
        if integrity["suspected_duplicate_count"]:
            issue_bits.append(
                f"疑似精確重複 {integrity['suspected_duplicate_count']} 列"
            )
        reasons = "".join(
            f"<li>{h(reason)}</li>"
            for reason in integrity["rejected_row_reasons"][:3]
        )
        reason_html = f"<ul>{reasons}</ul>" if reasons else ""
        integrity_html = (
            "<div class='alert'><b>資料完整性警告：</b>"
            f"{h('、'.join(issue_bits))}。目前數字只描述保留列，"
            "不得用來認證優勢或解鎖真錢階段。"
            f"{reason_html}</div>"
        )

    # 逐策略表(只有描述統計,不發徽章)
    tag_rows = ""
    for tv in (result.tag_verdicts or []):
        note = "(樣本少)" if tv.low_sample else ""
        tag_rows += (
            f"<tr><td>{h(tv.tag)}</td><td class='num'>{tv.n_trades}</td>"
            f"<td class='num'>{tv.expectancy:,.2f}{h(currency)}</td>"
            f"<td class='num'>{tv.total_pnl:,.2f}{h(currency)}</td>"
            f"<td>{h(tv.descriptor)} {h(note)}</td></tr>"
        )
    tag_table = (
        f"""<h2>各策略體檢(描述統計)</h2>
<table><thead><tr><th>策略標籤</th><th>筆數</th><th>每筆期望值</th>
<th>總損益</th><th>狀態</th></tr></thead><tbody>{tag_rows}</tbody></table>
<p class="muted">刻意不對個別策略做「具優勢」認證 —— 對多個策略各做一次統計檢定,
會把運氣誤認成優勢(策略越多、誤判機率越高)。</p>"""
        if tag_rows else ""
    )

    # 跟單成績單
    guru = ""
    if result.follow_guru is not None:
        guru = (
            f'<h2>跟單 / 聽明牌的成績單</h2><div class="alert">'
            f"{h(result.follow_guru.message)}</div>"
        )

    # 樣本外(誠實措辭,不宣稱裁決含 OOS)
    oos_html = (
        f'<h2>樣本外驗證</h2><p>{h(oos.headline)}</p><ul>'
        + "".join(f"<li>{h(x)}</li>" for x in oos.interpretation)
        + "</ul>"
    )

    flags = "".join(
        f'<li><b>[{h(rf.severity)}]</b> {h(rf.message)}</li>' for rf in v.red_flags
    )
    flags_html = f"<h2>偵測到的警訊</h2><ul>{flags}</ul>" if flags else ""

    # 未涵蓋成本 / 模型限制:HTML 是「分享出去」的載體,誠實警語
    # 不能只留在終端 —— 文字報告有、HTML 沒有 = 兩個通道誠實度不一致。
    from .markets import uncovered_cost_warnings as _ucw

    _cost_notes: list[str] = []
    for _mkt in sorted({t.market for t in result.log.trades}, key=lambda m: m.value):
        for _w in _ucw(_mkt):
            if _w not in _cost_notes:
                _cost_notes.append(_w)
    cost_html = (
        "<h2>本工具未涵蓋的成本 / 模型限制</h2><ul>"
        + "".join(f"<li>{h(w)}</li>" for w in _cost_notes) + "</ul>"
    ) if _cost_notes else ""

    # --full 選配區塊:時間趨勢(沿用 reliability 三態,不足的桶誠實不判讀)。
    # 標題跟著 granularity 走:紀錄跨 24 個月以上 analyze_trend 會自動改季度,
    # 把季度表硬標成「月報」是張冠李戴。
    trend_html = ""
    if trend is not None and not getattr(trend, "available", True):
        trend_html = (
            "<h2>時間趨勢</h2>"
            f"<div class='alert'>{h(trend.unavailable_reason)}</div>"
        )
    elif trend is not None and trend.buckets:
        period_word = "季" if trend.granularity == "quarter" else "月"
        rows = ""
        for b in trend.buckets:
            if b.reliability == "too_few":
                wr, exp = "—", "—"
                note = "資料不足,不判讀"
            else:
                wr = f"{b.win_rate:.0%}"
                exp = f"{b.expectancy:,.2f}"
                note = "樣本少,僅供參考" if b.reliability == "low" else ""
            rows += (
                f"<tr><td>{h(b.period_label)}</td><td class='num'>{b.n_trades}</td>"
                f"<td class='num'>{h(wr)}</td><td class='num'>{h(exp)}</td>"
                f"<td class='num'>{b.total_pnl:,.2f}</td><td>{h(note)}</td></tr>"
            )
        trend_html = (
            f"<h2>{period_word}報趨勢</h2>"
            f"<table><thead><tr><th>期間</th><th>筆數</th><th>勝率</th>"
            f"<th>每筆期望值</th><th>總損益</th><th>備註</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            f"<p>{h(trend.decay.headline)}</p>"
            f'<p class="muted">分{period_word}數字為描述統計;「看起來在跌」不是衰退證據,'
            f"以上方單一檢定的結論為準。</p>"
        )

    # --full 選配區塊:風險情境(警語必須跟著進來,不可只給數字)。
    # 比例一律走 format_fraction:0.9996 不可印成 100.0%(那是說「全爆」的假話)。
    scenario_html = ""
    if scenario is not None:
        from .montecarlo import format_fraction as _ff

        warn_items = "".join(f"<li>{h(w)}</li>" for w in scenario.warnings)
        inferred = "(⚠️ 工具粗估,非真實帳戶)" if scenario.start_equity_inferred else ""
        scenario_html = (
            f"<h2>風險情境模擬(如果未來長得像過去)</h2>"
            f"<table><tbody>"
            f"<tr><td>起始權益</td><td class='num'>{scenario.start_equity:,.0f} {h(inferred)}</td></tr>"
            f"<tr><td>爆倉路徑比例</td><td class='num'>{h(_ff(scenario.ruin_fraction))}</td></tr>"
            f"<tr><td>最大回撤(中位數 / 最壞 5%)</td>"
            f"<td class='num'>{h(_ff(scenario.median_max_drawdown))} / {h(_ff(scenario.p95_max_drawdown))}</td></tr>"
            f"<tr><td>連虧 10 次的機率</td><td class='num'>{h(_ff(scenario.losing_streak_10_prob))}</td></tr>"
            f"</tbody></table>"
            f'<div class="alert"><b>這是「情境」不是「預測」:</b><ul>{warn_items}</ul></div>'
        )
    elif scenario_note:
        # --full 但模擬被略過:分享出去的 HTML 必須寫明「略過+原因」,
        # 不能讓讀的人以為爆倉風險已評估過或不需要評估。
        scenario_html = (
            f"<h2>風險情境模擬(如果未來長得像過去)</h2>"
            f'<div class="alert">本次已略過風險情境模擬:{h(scenario_note)}。'
            f"這代表爆倉風險<b>尚未被評估</b>,不代表沒有風險。</div>"
        )

    sig = v.significance
    metric_notes = [
        note for note in (
            getattr(m, "return_note", ""),
            getattr(m, "drawdown_note", ""),
            getattr(m, "currency_note", ""),
        ) if note
    ]
    metric_alert = (
        "<div class='alert'>" + "<br>".join(h(note) for note in metric_notes) + "</div>"
        if metric_notes else ""
    )
    drawdown_display = "無法計算"
    if m.sequence_metrics_reliable:
        pct = (
            f"{m.max_drawdown_pct:.1%}"
            if m.drawdown_pct_reliable else "% 無法計算"
        )
        drawdown_display = f"{m.max_drawdown:,.2f}{currency}（{pct}）"
    if m.sequence_metrics_reliable:
        pnls = [t.pnl or 0.0 for t in result.log.sorted_by_time()]
        equity_html = f"<h2>累積損益曲線</h2>{_equity_svg(pnls)}"
    else:
        equity_html = (
            "<h2>累積損益曲線</h2>"
            "<div class='alert'>無法建立時序曲線："
            f"{h(m.sequence_note or '出場先後順序不可靠。')}"
            " 本工具不會用檔案列順序或佔位日期假裝時間。</div>"
        )

    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)}</title>
<style>
:root{{color-scheme:dark}}
body{{margin:0;background:#0d0f14;color:#d1d4dc;
font-family:system-ui,"Microsoft JhengHei",sans-serif;line-height:1.7}}
.wrap{{max-width:820px;margin:0 auto;padding:24px}}
h1{{font-size:22px;margin:0 0 4px}}
h2{{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #1e222d;padding-bottom:6px}}
.verdict{{background:#131722;border-left:5px solid {color};
padding:16px 18px;border-radius:8px;margin:16px 0}}
.verdict .lv{{color:{color};font-weight:700;font-size:18px}}
.stage{{background:{stage_bg};border:1px solid {stage_border};border-radius:8px;
padding:14px 16px;margin:12px 0 20px}}
.stage b{{color:{stage_text}}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{padding:8px 10px;border-bottom:1px solid #1e222d;text-align:left}}
th{{color:#8b94a3;font-weight:500}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.stat{{background:#131722;border:1px solid #1e222d;border-radius:8px;padding:12px 14px}}
.stat .k{{font-size:12px;color:#8b94a3}}
.stat .v{{font-size:19px;font-variant-numeric:tabular-nums;margin-top:2px}}
.alert{{background:#1a1310;border:1px solid #7f1d1d;border-radius:8px;padding:14px}}
.muted{{color:#6b7280;font-size:12px}}
.disclaimer{{margin-top:32px;padding-top:16px;border-top:1px solid #1e222d;
color:#6b7280;font-size:12px}}
</style></head><body><div class="wrap">

<h1>{h(title)}</h1>
<p class="muted">資料來源:{h(result.log.source)}</p>

<div class="verdict">
  <div class="lv">{h(v.level.badge)}</div>
  <div>{h(v.headline)}</div>
</div>

{integrity_html}

<div class="stage">
  <b>目前適合的階段</b><br>
  {h(stage.title)}<br>
  <span class="muted">{h(stage.reason)}</span>
</div>

<h2>核心績效</h2>
<div class="grid">
  <div class="stat"><div class="k">交易筆數</div><div class="v">{m.total_trades}</div></div>
  <div class="stat"><div class="k">勝率</div><div class="v">{m.win_rate:.1%}</div></div>
  <div class="stat"><div class="k">盈虧比</div><div class="v">{h(_fr(m.payoff_ratio))}</div></div>
  <div class="stat"><div class="k">每筆期望值</div><div class="v">{m.expectancy:,.2f}{h(currency)}</div></div>
  <div class="stat"><div class="k">總損益</div><div class="v">{m.total_pnl:,.2f}{h(currency)}</div></div>
  <div class="stat"><div class="k">最大回撤</div><div class="v">{h(drawdown_display)}</div></div>
</div>

{metric_alert}

{equity_html}

<h2>這是優勢,還是運氣?</h2>
<table><tbody>
<tr><td>每筆平均損益</td><td class="num">{sig.mean:,.2f}{h(currency)}</td></tr>
<tr><td>95% 信賴區間(雙尾)</td>
    <td class="num">[{sig.ci_low:,.2f}, {sig.ci_high:,.2f}]{h(currency)}</td></tr>
<tr><td>t 檢定 p 值</td><td class="num">{sig.p_value_t:.4f}</td></tr>
<tr><td>Bootstrap p 值(單尾)</td><td class="num">{sig.p_value_bootstrap:.4f}</td></tr>
</tbody></table>
<p class="muted">我們刻意不把 1−p 畫成「信心度」儀表:p 值不是「優勢為真的機率」。
信賴區間是否涵蓋 0,才是更誠實的判讀方式。</p>

{flags_html}
{tag_table}
{guru}
{oos_html}
{trend_html}
{scenario_html}
{cost_html}

<div class="disclaimer">
本報告為統計分析工具的輸出,僅供教育與研究用途,<b>不構成任何投資建議</b>。<br>
投資有風險,盈虧自負。過去績效不代表未來表現。<br>
本報告不做任何未來報酬的投射 —— 那正是投資詐騙的話術本體。
</div>
</div></body></html>"""


def render_share_card(result, *, width: int = 600) -> str:
    """分享圖卡:一張可截圖傳給家人的數據卡片。"""
    v = result.verdict
    m = result.metrics
    color = _verdict_color(v.level.value)
    from .onboarding import stage_from_analysis

    stage = stage_from_analysis(result)
    stage_bg, stage_border, stage_text = _stage_colors(stage.code)
    currency = str(getattr(m, "pnl_currency", "") or "幣別不明")
    if not getattr(m, "currency_reliable", False) and currency != "幣別不明":
        currency += "（推定）"
    guru_line = ""
    if result.follow_guru is not None and result.follow_guru.expectancy < 0:
        guru_line = (
            f'<div class="guru">聽老師 / 跟單的 {result.follow_guru.n_trades} 筆交易,'
            f"合計 {result.follow_guru.total_pnl:,.0f} {h(currency)}</div>"
        )
    integrity = result.log.integrity_as_dict()
    integrity_line = ""
    if not integrity["complete"]:
        bits = []
        if integrity["rejected_row_count"]:
            bits.append(f"拒絕 {integrity['rejected_row_count']} 列")
        if integrity["suspected_duplicate_count"]:
            bits.append(f"疑似精確重複 {integrity['suspected_duplicate_count']} 列")
        integrity_line = (
            '<div class="integrity"><b>資料完整性未通過：</b>'
            f"{h('、'.join(bits))}。本卡僅描述保留列，不可認證優勢。</div>"
        )

    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>反詐投資王 — 分享圖卡</title>
<style>
body{{margin:0;background:#0d0f14;display:flex;align-items:center;
justify-content:center;min-height:100vh;font-family:system-ui,"Microsoft JhengHei",sans-serif}}
.card{{width:{width}px;background:#131722;border-radius:16px;padding:28px 30px;
border-top:6px solid {color};color:#d1d4dc;box-shadow:0 8px 40px rgba(0,0,0,.5)}}
.badge{{color:{color};font-size:24px;font-weight:800}}
.head{{margin:10px 0 18px;font-size:15px;line-height:1.6}}
.stage{{margin:0 0 16px;padding:10px 12px;background:{stage_bg};
border:1px solid {stage_border};border-radius:8px;color:{stage_text};
font-size:13px;font-weight:700;line-height:1.5}}
.row{{display:flex;justify-content:space-between;padding:9px 0;
border-bottom:1px solid #1e222d;font-size:14px}}
.row b{{font-variant-numeric:tabular-nums}}
.guru{{margin-top:16px;padding:12px 14px;background:#1a1310;
border:1px solid #7f1d1d;border-radius:8px;font-size:13px;color:#fca5a5}}
.integrity{{margin:0 0 16px;padding:12px 14px;background:#1a1310;
border:1px solid #ef4444;border-radius:8px;font-size:13px;color:#fecaca;line-height:1.5}}
.foot{{margin-top:18px;font-size:11px;color:#6b7280;line-height:1.6}}
</style></head><body>
<div class="card">
  <div class="badge">{h(v.level.badge)}</div>
  <div class="head">{h(v.headline)}</div>
  {integrity_line}
  <div class="stage">目前適合的階段：{h(stage.title)}</div>
  <div class="row"><span>交易筆數</span><b>{m.total_trades}</b></div>
  <div class="row"><span>勝率</span><b>{m.win_rate:.0%}</b></div>
  <div class="row"><span>盈虧比</span><b>{h(_fr(m.payoff_ratio))}</b></div>
  <div class="row"><span>每筆期望值</span><b>{m.expectancy:,.0f} {h(currency)}</b></div>
  <div class="row"><span>總損益</span><b>{m.total_pnl:,.0f} {h(currency)}</b></div>
  {guru_line}
  <div class="foot">
    反詐投資王 · 用統計學判斷這是優勢還是賭博<br>
    本卡片為統計分析輸出,不構成投資建議。過去績效不代表未來表現。
  </div>
</div></body></html>"""
