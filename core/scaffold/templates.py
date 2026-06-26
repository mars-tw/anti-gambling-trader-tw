"""產出專案的各檔案範本。

每個函式回傳一個檔案的完整內容字串。範本刻意寫得「能直接跑」 ——
交易者 clone 下來、安裝依賴,執行 `python main.py` 就能看到紙上模擬
跑完並產生圖表。真實券商與真實規則則留待交易者填入。
"""

from __future__ import annotations

import json as _json


def readme(opts, chart_lib, broker_tmpl, discouraged: bool, verdict_headline: str) -> str:
    broker_name = broker_tmpl.name if broker_tmpl else "紙上模擬 PaperBroker(預設)"
    broker_install = broker_tmpl.sdk_install if broker_tmpl else "(無需額外安裝)"
    broker_note = f"\n> ⚠️ {broker_tmpl.notes}\n" if broker_tmpl else ""
    discourage_block = ""
    if discouraged:
        discourage_block = f"""
## ⛔ 來自反詐投資王的重要提醒

你的交易紀錄分析結果為:**{verdict_headline}**

因此本專案的 `main.py` 已**預設禁用真實下單**(`ALLOW_LIVE_TRADING = False`)。
請先讓你的策略通過統計與樣本外驗證,再手動解除 —— 這是保護你的錢,不是限制你。
"""

    return f"""# {opts.project_name}

由 **反詐投資王(Anti-Gambling Trader)** 腳架產生的個人交易程式。

- 市場:`{opts.market}`
- 標的:{', '.join(opts.symbols)}
- 券商:{broker_name}
- 圖表:{chart_lib.name}（{chart_lib.license}）
{discourage_block}
## 快速開始（紙上模擬，不碰真錢）

```bash
pip install -r requirements.txt
python main.py            # 用 PaperBroker 跑一遍,並產生圖表
```

跑完後會輸出績效摘要,並產生圖表檔（{chart_lib.kind} 形式）。

## 專案結構

```
{opts.project_name}/
  main.py            # 主程式（預設紙上模擬）
  strategy.py        # 你的交易規則（進出場條件待你填寫）
  broker_lib.py      # 自包含的券商函式庫（交易介面 + PaperBroker，零外部相依）
  broker_setup.py    # 選擇 / 建立券商連接器
  brokers/           # 真實券商範例框架（待填 API key 與實作）
  charting.py        # 圖表模組（{chart_lib.name}）
  data_feed.py       # 資料來源（回測 / 即時）
  config.example.yaml # 設定範本（複製成 config.yaml 後填入）
```

> 本專案**自包含**：不需安裝反詐投資王本體即可獨立執行。

## 接你自己的券商

券商:{broker_name}
安裝:`{broker_install}`
{broker_note}
1. 打開 `brokers/` 下的範例框架,填入你的 API key 與待實作的 `TODO`。
2. 在 `broker_setup.py` 把 `build_broker()` 改成回傳你的券商實例。
3. **務必先用券商的測試網 / 模擬模式**確認無誤。

## 從紙上模擬切到真實下單（高風險）

真實下單受**安全閘門**保護。要解除,必須:

1. 確認策略已通過反詐投資王的統計與樣本外驗證。
2. 在 `main.py` 把 `ALLOW_LIVE_TRADING` 改為 `True`。
3. 程式會要求你的券商呼叫 `confirm_live_trading(i_understand_the_risk=True)`。

這些摩擦是刻意設計的 —— 讓你在動用真錢前,被迫停下來想清楚。

## 換圖表樣式

想換成別的圖表庫,重新用反詐投資王產生:

```bash
python -m core.cli scaffold --name {opts.project_name} --broker {opts.broker} \\
    --chart <lightweight|plotly|mplfinance|echarts> --market {opts.market}
```

## ⚠️ 免責聲明

本專案為教育與研究用途,不構成投資建議。投資有風險,盈虧自負。
過去績效不代表未來表現。你對自己用本程式做出的一切交易負全部責任。

---

由 **反詐投資王(Anti-Gambling Trader)** 腳架產生。
原作者:好棒棒反詐協會 - 免費顧問 阿軒割割
"""


def config_yaml(opts, discouraged: bool, verdict_level: str) -> str:
    return f"""# {opts.project_name} 設定檔範本
# 複製成 config.yaml 後填入你的實際值。config.yaml 已被 .gitignore 排除。

market: {opts.market}
symbols:
{chr(10).join(f'  - {_json.dumps(s, ensure_ascii=False)}' for s in opts.symbols)}

broker: {opts.broker}        # paper | binance | ibkr | alpaca | shioaji

# 真實券商金鑰（紙上模擬不需要）。請勿提交到 git。
credentials:
  api_key: ""
  api_secret: ""

paper:
  starting_cash: 1000000
  fee_rate: 0.001
  slippage: 0.0005

risk:
  max_position_pct: 0.2     # 單一標的最多動用 20% 資金
  stop_loss_pct: 0.05       # 停損 5%（沒有停損是賭徒的標誌）
  take_profit_pct: 0.15     # 停利 15%

# 反詐投資王的裁決（產生時嵌入）
anti_gambling:
  verdict_level: "{verdict_level}"
  allow_live_trading: {str(not discouraged).lower()}   # 裁決勸退時預設 false
"""


def requirements(chart_lib, broker_tmpl) -> str:
    lines = ["# 本專案依賴", "pyyaml>=6.0"]
    if chart_lib.kind == "python":
        pkg = {"plotly": "plotly>=5.0",
               "mplfinance": "mplfinance>=0.12\npandas>=1.5"}.get(chart_lib.key)
        if pkg:
            lines.append(pkg)
    else:
        lines.append(f"# 圖表 {chart_lib.name} 由前端 CDN 載入,無需 pip 安裝")
    if broker_tmpl is not None:
        lines.append(f"# 你的券商 SDK（接真實券商時才需要）:")
        lines.append(f"# {broker_tmpl.sdk_install.replace('pip install ', '')}")
    return "\n".join(lines) + "\n"


def _safe_docstring(text: str) -> str:
    """消毒要嵌入三引號 docstring 的文字,避免破壞字串或注入。"""
    return str(text).replace("\\", "").replace('"""', "”””").replace("'''", "’’’")


def strategy_py(opts, discouraged: bool, verdict_level: str, verdict_headline: str) -> str:
    verdict_headline = _safe_docstring(verdict_headline)
    return f'''"""你的交易策略 —— 進出場規則待你填寫。

反詐投資王裁決:{verdict_level}
{verdict_headline}

提醒:如果你連『明確、可量化的進出場規則』都寫不出來,
那代表你的交易可能是憑感覺(也就是賭),而不是有方法。
寫得出規則,才有資格談自動化。
"""

from dataclasses import dataclass


@dataclass
class Signal:
    """策略對單一標的、單一時間點的決策。"""
    action: str          # "buy" | "sell" | "hold"
    reason: str = ""     # 進出場理由(會成為交易 tag,方便日後分析哪套邏輯有效)


class Strategy:
    def __init__(self, config: dict):
        self.cfg = config
        self.stop_loss = config.get("risk", {{}}).get("stop_loss_pct", 0.05)
        self.take_profit = config.get("risk", {{}}).get("take_profit_pct", 0.15)

    def on_bar(self, symbol: str, history: list, position) -> Signal:
        """每根 K 線呼叫一次,回傳決策。

        Args:
            symbol:   標的代號
            history:  到目前為止的 K 線清單(dict: time/open/high/low/close/volume)
            position: 目前持倉(None 表示空手)

        範例(均線交叉,僅供參考 —— 請換成你自己的規則):
            if len(history) < 20:
                return Signal("hold")
            closes = [h["close"] for h in history]
            ma_fast = sum(closes[-5:]) / 5
            ma_slow = sum(closes[-20:]) / 20
            if position is None and ma_fast > ma_slow:
                return Signal("buy", "5日均線上穿20日")
        """
        # ── 出場:停損 / 停利(預設邏輯,建議保留)──
        if position is not None and history:
            price = history[-1]["close"]
            entry = position.avg_price
            if price <= entry * (1 - self.stop_loss):
                return Signal("sell", "觸發停損")
            if price >= entry * (1 + self.take_profit):
                return Signal("sell", "觸發停利")

        # ── 進場:TODO 填入你的規則 ──
        return Signal("hold")
'''


def broker_setup_py(opts, broker_tmpl) -> str:
    if broker_tmpl is None:
        body = '''    # 預設:紙上模擬,不碰真錢。
    return PaperBroker(
        cash=config.get("paper", {}).get("starting_cash", 1_000_000),
        fee_rate=config.get("paper", {}).get("fee_rate", 0.001),
        slippage=config.get("paper", {}).get("slippage", 0.0005),
    )'''
        extra_import = ""
    else:
        cls = {"binance": "BinanceBroker", "ibkr": "IBKRBroker",
               "alpaca": "AlpacaBroker", "shioaji": "ShioajiBroker"}[broker_tmpl.key]
        extra_import = f"# from brokers.{broker_tmpl.key}_broker import {cls}\n"
        body = f'''    # 預設仍回傳紙上模擬;要接真實券商,取消下面註解並填入你的金鑰。
    if config.get("broker") == "{broker_tmpl.key}":
        # creds = config.get("credentials", {{}})
        # broker = {cls}(creds["api_key"], creds["api_secret"])  # 視券商調整參數
        # return broker
        pass
    return PaperBroker(
        cash=config.get("paper", {{}}).get("starting_cash", 1_000_000),
        fee_rate=config.get("paper", {{}}).get("fee_rate", 0.001),
    )'''

    return f'''"""券商選擇:預設紙上模擬,接真實券商時改這裡。"""

from broker_lib import PaperBroker
{extra_import}

def build_broker(config: dict):
    """依設定回傳券商實例。預設為安全的紙上模擬。"""
{body}
'''


def data_feed_py(opts) -> str:
    return '''"""資料來源:回測用歷史 K 線 / 即時報價的統一介面。

為了讓你立刻能跑,這裡內建一個確定性的「示範資料產生器」。
請換成你真正的資料來源:券商行情 API、yfinance、ccxt、或你自己的 CSV。
"""

import math


def load_history(symbol: str, n: int = 120) -> list:
    """回傳一段 K 線歷史(示範用,確定性、可重現)。"""
    candles = []
    base_ts = 1_700_000_000
    price = 100.0
    for i in range(n):
        drift = math.sin(i / 7.0) * 5 + i * 0.12
        o = price
        c = 100 + drift
        h = max(o, c) + abs(math.sin(i)) * 1.2 + 0.4
        low = min(o, c) - abs(math.cos(i)) * 1.2 - 0.4
        candles.append({
            "time": base_ts + i * 86400,
            "open": round(o, 2), "high": round(h, 2),
            "low": round(low, 2), "close": round(c, 2),
            "volume": 1000 + (i % 9) * 100,
        })
        price = c
    return candles
    # TODO: 換成真實資料,例如:
    #   import yfinance as yf; df = yf.download(symbol, period="6mo")
    #   import ccxt; ohlcv = ccxt.binance().fetch_ohlcv(symbol, "1d")
'''


def main_py(opts, chart_lib, broker_tmpl, discouraged: bool) -> str:
    # 真實下單一律預設關閉(安全優先),與裁決無關 —— 即使裁決為「具優勢」,
    # 也要使用者親手把這個常數改成 True,逼他停下來想清楚。
    allow_live = "False"
    out_ext = "png" if chart_lib.key == "mplfinance" else "html"
    out_arg = "out_png" if chart_lib.key == "mplfinance" else "out_html"
    return f'''"""主程式 —— 預設用紙上模擬跑一遍策略,並產生圖表。

安全設計:
  - ALLOW_LIVE_TRADING 預設為 False。即使你接了真實券商,
    也要明確改成 True 並通過安全閘門,才會真的下真錢訂單。
"""

import os

import yaml

from strategy import Strategy
from broker_setup import build_broker
from data_feed import load_history
from charting import render
from broker_lib import Order, OrderSide, BrokerAdapter

# ── 真實下單總開關(預設關閉,保護你的錢)──
ALLOW_LIVE_TRADING = {allow_live}


def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        # 沒有 config.yaml 時用內建預設(紙上模擬)
        return {{
            "market": {opts.market!r},
            "symbols": {opts.symbols!r},
            "broker": "paper",
            "paper": {{"starting_cash": 1_000_000, "fee_rate": 0.001}},
            "risk": {{"stop_loss_pct": 0.05, "take_profit_pct": 0.15,
                      "max_position_pct": 0.2}},
        }}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def maybe_enable_live(broker: BrokerAdapter, config: dict) -> None:
    """若使用者明確開啟真實下單,解除安全閘門;否則維持封鎖。

    兩道彼此獨立的閘門都通過才會放行:
      1. main.py 的 ALLOW_LIVE_TRADING 常數(要手動改成 True)
      2. config.yaml 的 risk.i_have_read_disclaimer 設為 true
    這樣「改一個常數」無法單獨解鎖,逼你在兩個不同地方都明確表態。
    """
    if not getattr(broker, "is_live", False):
        return  # 紙上模擬,無需解鎖

    if not ALLOW_LIVE_TRADING:
        raise SystemExit(
            "⛔ 偵測到真實券商,但 ALLOW_LIVE_TRADING 為 False。\\n"
            "   這是保護你的錢。確認策略已驗證、願意自負風險後,\\n"
            "   再把 main.py 的 ALLOW_LIVE_TRADING 改成 True。"
        )

    if not config.get("risk", {{}}).get("i_have_read_disclaimer", False):
        raise SystemExit(
            "⛔ 真實下單的第二道閘門未解除。\\n"
            "   請先閱讀免責聲明,並在 config.yaml 的 risk 區塊加上:\\n"
            "       i_have_read_disclaimer: true\\n"
            "   兩道閘門刻意分開,確保你不是只改了一個地方就誤觸真錢下單。"
        )

    broker.confirm_live_trading(i_understand_the_risk=True)


def run():
    config = load_config()
    broker = build_broker(config)
    broker.connect()
    maybe_enable_live(broker, config)

    strategy = Strategy(config)
    symbols = config.get("symbols", {opts.symbols!r})
    risk = config.get("risk", {{}})
    max_pct = risk.get("max_position_pct", 0.2)

    all_markers = []
    equity_curve = []
    candles_for_chart = []

    for symbol in symbols:
        history = load_history(symbol)
        candles_for_chart = history  # 圖表以最後一檔為例
        for i in range(len(history)):
            window = history[: i + 1]
            bar = window[-1]
            # 紙上模擬需要餵價
            if hasattr(broker, "set_price"):
                broker.set_price(symbol, bar["close"])

            positions = {{p.symbol: p for p in broker.get_positions()}}
            pos = positions.get(symbol)
            sig = strategy.on_bar(symbol, window, pos)

            if sig.action == "buy" and pos is None:
                acct = broker.get_account()
                budget = acct.equity * max_pct
                qty = max(1, int(budget / bar["close"]))
                r = broker.place_order(Order(symbol, OrderSide.BUY, qty,
                                             client_tag=sig.reason))
                if r.ok:
                    all_markers.append({{"time": bar["time"], "price": bar["low"],
                                         "side": "buy", "text": sig.reason or "買"}})
            elif sig.action == "sell" and pos is not None:
                r = broker.place_order(Order(symbol, OrderSide.SELL, abs(pos.quantity),
                                             client_tag=sig.reason))
                if r.ok:
                    all_markers.append({{"time": bar["time"], "price": bar["high"],
                                         "side": "sell", "text": sig.reason or "賣"}})

            equity_curve.append({{"time": bar["time"],
                                  "value": broker.get_account().equity}})

    acct = broker.get_account()
    print("=" * 50)
    print(f"  策略執行完畢（{{config.get('broker')}} 模式）")
    print(f"  最終權益: {{acct.equity:,.2f}}")
    print(f"  成交筆數: {{len(all_markers)}}")
    print("=" * 50)

    out = render(candles_for_chart, all_markers, equity_curve,
                 {out_arg}="chart.{out_ext}", title={opts.project_name!r})
    print(f"  圖表已產生: {{out}}")
    if getattr(broker, "is_live", False):
        print("  ⚠️ 這是真實下單模式,以上為真實訂單結果。")


if __name__ == "__main__":
    run()
'''


def gitignore() -> str:
    return """# 金鑰與個人設定 —— 絕對不要提交
config.yaml
.env
*.key
credentials*

# 產出
chart.html
chart.png
chart_data.json

# Python
__pycache__/
*.pyc
.venv/
venv/
"""
