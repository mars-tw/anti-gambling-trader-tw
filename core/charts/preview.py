"""圖表樣式預覽介面。

產生一個單一 HTML,用同一份範例 K 線資料,把四種開源圖表庫的實際樣式
並排呈現,讓交易者「先看到、再選擇」。每個樣式下方附一個「選這個」的
指令提示(對應 scaffold 時要帶的 --chart 參數)。
"""

from __future__ import annotations

import json
import math
from .registry import list_chart_libs


def _sample_candles(n: int = 60) -> list[dict]:
    """產生一段確定性的範例 K 線(不依賴亂數,結果可重現)。"""
    candles = []
    price = 100.0
    base_ts = 1_700_000_000  # 固定起始時間,確保輸出可重現
    for i in range(n):
        # 用正弦 + 緩升,模擬有波段的走勢
        drift = math.sin(i / 6.0) * 4 + i * 0.15
        o = price
        c = 100 + drift
        h = max(o, c) + abs(math.sin(i)) * 1.5 + 0.5
        low = min(o, c) - abs(math.cos(i)) * 1.5 - 0.5
        candles.append({
            "time": base_ts + i * 86400,
            "open": round(o, 2), "high": round(h, 2),
            "low": round(low, 2), "close": round(c, 2),
            "volume": 1000 + (i % 7) * 120,
        })
        price = c
    return candles


def _sample_markers(candles: list[dict]) -> list[dict]:
    """在範例 K 線上放幾個買賣標記。"""
    return [
        {"time": candles[10]["time"], "price": candles[10]["low"], "side": "buy", "text": "買"},
        {"time": candles[25]["time"], "price": candles[25]["high"], "side": "sell", "text": "賣"},
        {"time": candles[38]["time"], "price": candles[38]["low"], "side": "buy", "text": "買"},
        {"time": candles[52]["time"], "price": candles[52]["high"], "side": "sell", "text": "賣"},
    ]


def _sample_equity(candles: list[dict]) -> list[dict]:
    eq = 1_000_000.0
    out = []
    for i, c in enumerate(candles):
        eq += (c["close"] - c["open"]) * 800
        out.append({"time": c["time"], "value": round(eq, 2)})
    return out


def build_preview_page(out_html: str = "chart_preview.html") -> str:
    """產生四種圖表庫並排的樣式預覽 HTML。"""
    candles = _sample_candles()
    markers = _sample_markers(candles)
    equity = _sample_equity(candles)

    data_json = json.dumps(
        {"candles": candles, "markers": markers, "equity": equity},
        ensure_ascii=False,
    )

    cards = []
    for lib in list_chart_libs():
        cards.append(f"""
        <section class="card">
          <header>
            <h2>{lib.name}</h2>
            <span class="meta">{lib.license} · {lib.kind} · {lib.blurb}</span>
          </header>
          <div class="frame" id="frame-{lib.key}"></div>
          <footer>
            選這個樣式 → <code>--chart {lib.key}</code>
          </footer>
        </section>""")

    html = _PREVIEW_TEMPLATE.replace("__CARDS__", "".join(cards))
    html = html.replace("__DATA__", data_json)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return out_html


# 預覽頁:Lightweight Charts 與 ECharts 直接 CDN 即時渲染;
# Plotly / mplfinance 為 Python 端產出,這裡用靜態示意圖呈現其風格,
# 並標明「實際樣式以產生的專案為準」。
_PREVIEW_TEMPLATE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>反詐投資王 — 圖表樣式預覽</title>
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts/dist/echarts.min.js"></script>
<style>
:root{color-scheme:dark}
body{margin:0;background:#0d0f14;color:#d1d4dc;font-family:system-ui,"Microsoft JhengHei",sans-serif}
.top{padding:20px 24px;border-bottom:1px solid #1e222d}
.top h1{margin:0 0 6px;font-size:20px}
.top p{margin:0;color:#8b94a3;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px;padding:16px}
.card{background:#131722;border:1px solid #1e222d;border-radius:10px;overflow:hidden}
.card header{padding:12px 14px;border-bottom:1px solid #1e222d}
.card h2{margin:0;font-size:15px}
.card .meta{font-size:11px;color:#6b7280}
.frame{height:280px}
.card footer{padding:10px 14px;border-top:1px solid #1e222d;font-size:13px;color:#9aa4b2}
.card code{background:#0d0f14;padding:2px 8px;border-radius:5px;color:#26a69a;border:1px solid #1e222d}
.note{padding:14px 24px;color:#6b7280;font-size:12px}
canvas,div.frame>div{width:100%!important}
</style></head>
<body>
<div class="top">
  <h1>📊 圖表樣式預覽 — 挑一個你喜歡的</h1>
  <p>同一份範例資料、四種開源圖表庫。看完後,用下方的 <code>--chart &lt;key&gt;</code> 產生你的交易程式。</p>
</div>
<div class="grid">__CARDS__</div>
<div class="note">
  說明:Lightweight Charts 與 ECharts 為前端即時渲染,所見即所得。
  Plotly 與 mplfinance 為 Python 端產出,此處呈現其風格示意,實際成品以產生的專案為準。
</div>
<script>
const DATA = __DATA__;
const lwOpt = {layout:{background:{color:'#131722'},textColor:'#d1d4dc'},
  grid:{vertLines:{color:'#1e222d'},horzLines:{color:'#1e222d'}},
  timeScale:{borderColor:'#1e222d'},rightPriceScale:{borderColor:'#1e222d'}};

// Lightweight Charts
(function(){
  const el=document.getElementById('frame-lightweight'); if(!el)return;
  const c=LightweightCharts.createChart(el,{...lwOpt,height:280});
  const s=c.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',
    borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
  s.setData(DATA.candles);
  s.setMarkers(DATA.markers.map(m=>({time:m.time,
    position:m.side==='buy'?'belowBar':'aboveBar',
    color:m.side==='buy'?'#26a69a':'#ef5350',
    shape:m.side==='buy'?'arrowUp':'arrowDown',text:m.text})));
  new ResizeObserver(()=>c.applyOptions({width:el.clientWidth})).observe(el);
  c.applyOptions({width:el.clientWidth});
})();

// ECharts
(function(){
  const el=document.getElementById('frame-echarts'); if(!el)return;
  const c=echarts.init(el,'dark',{height:280});
  c.setOption({backgroundColor:'#131722',
    xAxis:{data:DATA.candles.map(x=>x.time),show:false},yAxis:{scale:true},
    grid:{left:40,right:10,top:10,bottom:20},
    series:[{type:'candlestick',
      data:DATA.candles.map(x=>[x.open,x.close,x.low,x.high]),
      itemStyle:{color:'#26a69a',color0:'#ef5350',borderColor:'#26a69a',borderColor0:'#ef5350'},
      markPoint:{symbolSize:36,data:DATA.markers.map(m=>({
        coord:[DATA.candles.findIndex(x=>x.time===m.time),m.price],
        value:m.side==='buy'?'買':'賣',
        itemStyle:{color:m.side==='buy'?'#26a69a':'#ef5350'}}))}}]});
  addEventListener('resize',()=>c.resize());
})();

// Plotly / mplfinance 風格示意(用 canvas 畫簡易 K 線,標明為示意)
function sketch(id,bg,note){
  const el=document.getElementById(id); if(!el)return;
  el.style.position='relative';el.style.background=bg;
  const cv=document.createElement('canvas');
  cv.width=el.clientWidth||420;cv.height=280;el.appendChild(cv);
  const x=cv.getContext('2d');const cs=DATA.candles;
  const hi=Math.max(...cs.map(c=>c.high)),lo=Math.min(...cs.map(c=>c.low));
  const w=cv.width/cs.length;const Y=v=>270-(v-lo)/(hi-lo)*250;
  cs.forEach((c,i)=>{const px=i*w+w/2;
    x.strokeStyle=c.close>=c.open?'#26a69a':'#ef5350';x.beginPath();
    x.moveTo(px,Y(c.high));x.lineTo(px,Y(c.low));x.stroke();
    x.fillStyle=x.strokeStyle;const t=Y(Math.max(c.open,c.close));
    x.fillRect(px-w*0.3,t,w*0.6,Math.max(2,Math.abs(Y(c.open)-Y(c.close))));});
  const tag=document.createElement('div');
  tag.textContent=note;tag.style.cssText=
    'position:absolute;top:8px;left:10px;font-size:11px;color:#6b7280';
  el.appendChild(tag);
}
sketch('frame-plotly','#111','▲ Plotly 風格示意(實際為互動式)');
sketch('frame-mplfinance','#fafafa','▲ mplfinance 風格示意(實際為 PNG 圖檔)');
</script>
</body></html>"""
