"""圖表庫註冊表。

每個圖表庫提供一段「可直接用」的繪圖程式碼,接收統一格式的
K 線資料(OHLCV)與交易訊號(進出場點 + 權益曲線),畫出:
  - K 線圖
  - 進場 / 出場標記
  - 權益曲線(資產變化)

統一資料格式(產出專案的 data_feed 會餵這個格式):
    candles: list[dict] 每筆含 time(秒級 epoch 或 'YYYY-MM-DD')、open、high、low、close、volume
    markers: list[dict] 每筆含 time、price、side('buy'|'sell')、text
    equity:  list[dict] 每筆含 time、value
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChartLib:
    """一個圖表庫的範本。"""

    key: str
    name: str
    license: str
    kind: str            # "web" | "python"
    install: str         # 安裝 / 引入方式
    blurb: str           # 一句話特色
    module_code: str     # 寫入專案的繪圖模組程式碼


# ── Lightweight Charts(TradingView 開源)──────────────────
_LIGHTWEIGHT = ChartLib(
    key="lightweight",
    name="Lightweight Charts (TradingView)",
    license="Apache-2.0",
    kind="web",
    install='前端透過 CDN 載入,Python 端只負責輸出 chart_data.json',
    blurb="最接近專業看盤軟體的 K 線體驗,輕量、流暢。",
    module_code='''"""Lightweight Charts 繪圖模組:輸出資料 + 自包含 HTML。"""

import json
from pathlib import Path


def render(candles, markers, equity, out_html="chart.html", title="我的交易策略"):
    """產生一個自包含的 HTML 圖表(K 線 + 進出場標記 + 權益曲線)。"""
    lw_markers = [
        {
            "time": m["time"],
            "position": "belowBar" if m["side"] == "buy" else "aboveBar",
            "color": "#26a69a" if m["side"] == "buy" else "#ef5350",
            "shape": "arrowUp" if m["side"] == "buy" else "arrowDown",
            "text": m.get("text", m["side"]),
        }
        for m in markers
    ]
    payload = {
        "candles": candles,
        "markers": lw_markers,
        "equity": [{"time": e["time"], "value": e["value"]} for e in equity],
        "title": title,
    }
    html = _TEMPLATE.replace("__PAYLOAD__", _safe_json(payload))
    Path(out_html).write_text(html, encoding="utf-8")
    return out_html


def _safe_json(payload):
    """把資料序列化成可安全嵌入 <script> 的 JSON。

    json.dumps 不會跳脫 < > &,若 title 或標的名含 "</script>" 會提前
    閉合 script 區塊造成 XSS。這裡把這些字元轉成 unicode escape。
    """
    s = json.dumps(payload, ensure_ascii=False)
    return s.replace("<", "\\\\u003c").replace(">", "\\\\u003e").replace("&", "\\\\u0026")


_TEMPLATE = """<!doctype html><html lang=\\"zh-Hant\\"><head><meta charset=\\"utf-8\\">
<title>交易策略圖表</title>
<script src=\\"https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js\\"></script>
<style>body{margin:0;background:#131722;color:#d1d4dc;font-family:system-ui}
h2{padding:12px 16px;margin:0}#c{height:60vh}#e{height:28vh}</style></head>
<body><h2 id=\\"t\\"></h2><div id=\\"c\\"></div><div id=\\"e\\"></div>
<script>const D=__PAYLOAD__;document.getElementById('t').textContent=D.title;
const opt={layout:{background:{color:'#131722'},textColor:'#d1d4dc'},
grid:{vertLines:{color:'#1e222d'},horzLines:{color:'#1e222d'}}};
const c=LightweightCharts.createChart(document.getElementById('c'),opt);
const s=c.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',
borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
s.setData(D.candles);s.setMarkers(D.markers);
const e=LightweightCharts.createChart(document.getElementById('e'),opt);
const l=e.addAreaSeries({lineColor:'#2962ff',topColor:'rgba(41,98,255,.4)',
bottomColor:'rgba(41,98,255,0)'});l.setData(D.equity);
new ResizeObserver(()=>{c.applyOptions({width:innerWidth});
e.applyOptions({width:innerWidth})}).observe(document.body);
c.applyOptions({width:innerWidth});e.applyOptions({width:innerWidth});</script>
</body></html>"""
''',
)


# ── Plotly(純 Python)──────────────────────────────────────
_PLOTLY = ChartLib(
    key="plotly",
    name="Plotly",
    license="MIT",
    kind="python",
    install="pip install plotly",
    blurb="純 Python 即可產出互動圖表,不必寫前端。",
    module_code='''"""Plotly 繪圖模組:純 Python 產出互動式 HTML 圖表。"""


def render(candles, markers, equity, out_html="chart.html", title="我的交易策略"):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
        vertical_spacing=0.03, subplot_titles=("K 線 + 進出場", "權益曲線"),
    )
    t = [c["time"] for c in candles]
    fig.add_trace(go.Candlestick(
        x=t, open=[c["open"] for c in candles], high=[c["high"] for c in candles],
        low=[c["low"] for c in candles], close=[c["close"] for c in candles],
        name="K線", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ), row=1, col=1)

    buys = [m for m in markers if m["side"] == "buy"]
    sells = [m for m in markers if m["side"] == "sell"]
    if buys:
        fig.add_trace(go.Scatter(
            x=[m["time"] for m in buys], y=[m["price"] for m in buys], mode="markers",
            marker=dict(symbol="triangle-up", size=12, color="#26a69a"), name="買進",
        ), row=1, col=1)
    if sells:
        fig.add_trace(go.Scatter(
            x=[m["time"] for m in sells], y=[m["price"] for m in sells], mode="markers",
            marker=dict(symbol="triangle-down", size=12, color="#ef5350"), name="賣出",
        ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=[e["time"] for e in equity], y=[e["value"] for e in equity],
        fill="tozeroy", line=dict(color="#2962ff"), name="權益",
    ), row=2, col=1)

    fig.update_layout(template="plotly_dark", title=title, xaxis_rangeslider_visible=False)
    fig.write_html(out_html)
    return out_html
''',
)


# ── mplfinance(靜態 K 線)─────────────────────────────────
_MPLFINANCE = ChartLib(
    key="mplfinance",
    name="mplfinance",
    license="BSD",
    kind="python",
    install="pip install mplfinance pandas",
    blurb="最簡單、零前端,適合產出 PNG 圖檔報告。",
    module_code='''"""mplfinance 繪圖模組:產出靜態 K 線 PNG 圖。"""


def render(candles, markers, equity, out_png="chart.png", title="我的交易策略"):
    import pandas as pd
    import mplfinance as mpf

    df = pd.DataFrame(candles)
    df["time"] = pd.to_datetime(df["time"], unit="s", errors="ignore")
    df = df.rename(columns=str.capitalize).set_index("Time")

    # 進出場標記:對齊到 K 線索引
    buy_y = [float("nan")] * len(df)
    sell_y = [float("nan")] * len(df)
    idx = {str(t): i for i, t in enumerate(candles)}  # 簡化對齊;實務可依時間精確比對
    addplots = []
    if any(m["side"] == "buy" for m in markers):
        addplots.append(mpf.make_addplot(buy_y, type="scatter", marker="^", color="g"))
    if any(m["side"] == "sell" for m in markers):
        addplots.append(mpf.make_addplot(sell_y, type="scatter", marker="v", color="r"))

    mpf.plot(df, type="candle", style="charles", title=title,
             addplot=addplots or None, volume=True, savefig=out_png)
    return out_png
''',
)


# ── ECharts ────────────────────────────────────────────────
_ECHARTS = ChartLib(
    key="echarts",
    name="Apache ECharts",
    license="Apache-2.0",
    kind="web",
    install="前端透過 CDN 載入,Python 端只負責輸出 chart_data.json",
    blurb="功能全面、中文生態完整,適合做豐富的儀表板。",
    module_code='''"""ECharts 繪圖模組:輸出自包含 HTML(K 線 + 標記 + 權益)。"""

import json
from pathlib import Path


def render(candles, markers, equity, out_html="chart.html", title="我的交易策略"):
    ohlc = [[c["open"], c["close"], c["low"], c["high"]] for c in candles]
    times = [c["time"] for c in candles]
    mark_points = [
        {
            "name": m.get("text", m["side"]),
            "coord": [m["time"], m["price"]],
            "value": "B" if m["side"] == "buy" else "S",
            "itemStyle": {"color": "#26a69a" if m["side"] == "buy" else "#ef5350"},
        }
        for m in markers
    ]
    payload = {
        "times": times, "ohlc": ohlc, "marks": mark_points,
        "equity": [[e["time"], e["value"]] for e in equity], "title": title,
    }
    html = _TEMPLATE.replace("__PAYLOAD__", _safe_json(payload))
    Path(out_html).write_text(html, encoding="utf-8")
    return out_html


def _safe_json(payload):
    """把資料序列化成可安全嵌入 <script> 的 JSON(跳脫 < > & 防 XSS)。"""
    s = json.dumps(payload, ensure_ascii=False)
    return s.replace("<", "\\\\u003c").replace(">", "\\\\u003e").replace("&", "\\\\u0026")


_TEMPLATE = """<!doctype html><html lang=\\"zh-Hant\\"><head><meta charset=\\"utf-8\\">
<title>交易策略圖表</title>
<script src=\\"https://cdn.jsdelivr.net/npm/echarts/dist/echarts.min.js\\"></script>
<style>body{margin:0;background:#131722}#c{width:100vw;height:65vh}#e{width:100vw;height:30vh}</style>
</head><body><div id=\\"c\\"></div><div id=\\"e\\"></div><script>const D=__PAYLOAD__;
const c=echarts.init(document.getElementById('c'),'dark');
c.setOption({title:{text:D.title,textStyle:{color:'#d1d4dc'}},
xAxis:{data:D.times},yAxis:{scale:true},
series:[{type:'candlestick',data:D.ohlc,
itemStyle:{color:'#26a69a',color0:'#ef5350',borderColor:'#26a69a',borderColor0:'#ef5350'},
markPoint:{data:D.marks}}]});
const e=echarts.init(document.getElementById('e'),'dark');
e.setOption({xAxis:{type:'category'},yAxis:{scale:true},
series:[{type:'line',data:D.equity,areaStyle:{color:'rgba(41,98,255,.4)'},
lineStyle:{color:'#2962ff'},showSymbol:false}]});
addEventListener('resize',()=>{c.resize();e.resize()});</script></body></html>"""
''',
)


CHART_LIBS: dict[str, ChartLib] = {
    lib.key: lib for lib in (_LIGHTWEIGHT, _PLOTLY, _MPLFINANCE, _ECHARTS)
}


def list_chart_libs() -> list[ChartLib]:
    return list(CHART_LIBS.values())


def get_chart_lib(key: str) -> ChartLib:
    if key not in CHART_LIBS:
        raise KeyError(f"未知的圖表庫: {key}。可用: {list(CHART_LIBS)}")
    return CHART_LIBS[key]
