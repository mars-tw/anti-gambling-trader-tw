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
    broker_validation = (
        "Pionex 官方現貨 API 未列 sandbox；只能先用本機 PaperBroker 驗證。"
        if broker_tmpl and broker_tmpl.key == "pionex"
        else "務必先用券商的測試網 / 模擬模式確認無誤。"
    )
    discourage_block = ""
    if discouraged:
        discourage_block = f"""
## ⛔ 來自反詐投資王的重要提醒

你的交易紀錄分析結果為:**{verdict_headline}**

因此本專案的 `main.py` 已**預設禁用真實下單**(`ALLOW_LIVE_TRADING = False`)。
請先修正方法,用後續未看過的新交易重新驗證,再以 `--from-analysis` 重新產生專案。
不要手動猜測或改寫 `stage_code` / `allow_live_trading` —— 這是保護你的錢,不是限制你。
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
1. 打開 `brokers/` 下的範例框架,依說明用環境變數或設定提供連線資料,並完成 `TODO`。
2. 在 `broker_setup.py` 把 `build_broker()` 改成回傳你的券商實例。
3. **{broker_validation}**

## 從紙上模擬切到真實下單（高風險）

真實下單受**安全閘門**保護。要解除,必須:

1. 用 `scaffold --from-analysis <完整交易紀錄>` 產生專案;只有階段為
   `tiny_live_validation` 時,設定範本才可能同時寫入正確 stage 與允許旗標。
2. 複製 `config.example.yaml` 為 `config.yaml`,閱讀免責聲明後才把
   `risk.i_have_read_disclaimer` 設為布林值 `true`。不要手動改 stage 或允許旗標。
3. 在 `main.py` 把 `ALLOW_LIVE_TRADING` 改為 `True`。
4. Runtime 會重新驗證上述所有設定,最後才呼叫券商的
   `confirm_live_trading(i_understand_the_risk=True)`。
5. `main.py` 內建的是歷史／示範 K 線 replay，因此偵測到 live broker 時仍會硬性退出；
   不會把 120 根歷史訊號一次送成真單。真實驗證必須另寫只處理「最新一根已完成 K 線」
   的 runner，並接上可證明為即時且無前視的資料來源。

缺欄位、錯誤型別、其他 stage 或 YAML 損壞都會維持封鎖。即使通過全部閘門,
也只代表程式允許你自行做極小額驗證,不代表適合重押或全職交易。

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


def _broker_keys_comment() -> str:
    """動態產生券商清單註解 —— 硬編碼清單已經漂移過一次(缺 kgi 等)。"""
    from ..broker import BROKER_TEMPLATES
    return " | ".join(["paper"] + sorted(BROKER_TEMPLATES.keys()))


# 各券商建構子需要的 credentials 欄位；Pionex 只保存環境變數名稱，不落盤保存 key。
# 固定產 api_key/api_secret 會讓 13 個券商有 9 個照表填卻對不上建構子
# (第 8 輪券商層稽核逐一實測的簽名)。
_CREDENTIAL_FIELDS: dict[str, list[str]] = {
    "binance": ['api_key: ""', 'api_secret: ""', "testnet: true"],
    "ibkr": ['host: "127.0.0.1"', "port: 7497", "client_id: 1"],
    "alpaca": ['api_key: ""', 'api_secret: ""', "paper: true"],
    "shioaji": ['api_key: ""', 'secret_key: ""', "simulation: true"],
    "yuanta": ['account: ""', 'password: ""', "simulation: true"],
    "fubon": ['account_id: ""', 'password: ""', 'cert_path: ""', 'cert_password: ""'],
    "kgi": ['account: ""', 'password: ""', "simulation: true"],
    "tw_futures": ['account: ""', 'password: ""', 'broker: ""'],
    "ccxt": ['exchange_id: "binance"', 'api_key: ""', 'api_secret: ""',
             'password: ""', "sandbox: true"],
    "okx": ['api_key: ""', 'api_secret: ""', 'passphrase: ""', "sandbox: true"],
    "bybit": ['api_key: ""', 'api_secret: ""', "testnet: true"],
    "pionex": ['api_key_env: "PIONEX_API_KEY"',
               'api_secret_env: "PIONEX_API_SECRET"',
               'quote_currency: "USDT"', "timeout: 10.0"],
    "tradier": ['access_token: ""', 'account_id: ""', "sandbox: true"],
}


def _credentials_block(broker_key: str) -> str:
    fields = _CREDENTIAL_FIELDS.get(broker_key, ['api_key: ""', 'api_secret: ""'])
    return "\n".join(f"  {f}" for f in fields)


def config_yaml(opts, discouraged: bool, verdict_level: str) -> str:
    stage_code = getattr(getattr(opts, "stage", None), "code", "unverified")
    stage_reason = getattr(
        getattr(opts, "stage", None),
        "reason",
        "尚未完成含樣本外與風險基準的交易階段分析",
    )
    live_allowed = bool(
        not discouraged and stage_code == "tiny_live_validation"
    )
    return f"""# {opts.project_name} 設定檔範本
# 複製成 config.yaml 後填入你的實際值。config.yaml 已被 .gitignore 排除。

market: {opts.market}
symbols:
{chr(10).join(f'  - {_json.dumps(s, ensure_ascii=False)}' for s in opts.symbols)}

broker: {opts.broker}        # 可選:{_broker_keys_comment()}

# 真實券商連線資訊（紙上模擬不需要）。Pionex 只填環境變數名稱；其他範本欄位
# 對應 brokers/ 內的建構子簽名。
# 請勿提交到 git。
credentials:
{_credentials_block(opts.broker)}

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
  stage_code: {_json.dumps(stage_code, ensure_ascii=False)}
  stage_reason: {_json.dumps(stage_reason, ensure_ascii=False)}
  allow_live_trading: {str(live_allowed).lower()}   # 只有完整 stage 為 tiny_live_validation 才可能 true
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
        # 某些現貨 balance API 只有數量、沒有成本價。未知成本時用示範 K 線
        # 自動停損/停利可能立刻賣掉真實持倉，因此一律 hold，等使用者補可靠成本。
        if position is not None and not getattr(position, "cost_basis_known", True):
            return Signal("hold", "成本基準未知，禁止自動停損／停利")

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
        # 從範本自動取得類別名,新增券商不必在此維護對照表
        cls = broker_tmpl.class_name
        extra_import = f"# from brokers.{broker_tmpl.key}_broker import {cls}\n"
        if broker_tmpl.key == "pionex":
            body = f'''    # 預設仍回傳紙上模擬。Pionex 官方現貨 API 沒有文件化 sandbox，
    # 所以金鑰只從環境變數讀取，不寫進 config.yaml。
    if config.get("broker") == "pionex":
        # import os
        # creds = config.get("credentials", {{}})
        # broker = {cls}(
        #     api_key=os.environ[creds.get("api_key_env", "PIONEX_API_KEY")],
        #     api_secret=os.environ[creds.get("api_secret_env", "PIONEX_API_SECRET")],
        #     quote_currency=creds.get("quote_currency", "USDT"),
        #     timeout=creds.get("timeout", 10.0),
        # )
        # return broker
        pass
    return PaperBroker(
        cash=config.get("paper", {{}}).get("starting_cash", 1_000_000),
        fee_rate=config.get("paper", {{}}).get("fee_rate", 0.001),
    )'''
        else:
            body = f'''    # 預設仍回傳紙上模擬;要接真實券商,取消下面註解並填入你的金鑰。
    if config.get("broker") == "{broker_tmpl.key}":
        # creds = config.get("credentials", {{}})
        # ⚠️ 建構子參數依券商而異(如 IBKR 是 host/port/client_id,不是金鑰)——
        #    先打開 brokers/{broker_tmpl.key}_broker.py 看 {cls}.__init__ 的簽名,
        #    再把對應欄位加進 config.yaml 的 credentials 區塊。
        # broker = {cls}(...)  # ← 依上面確認的簽名填參數
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
import sys
from decimal import Decimal, InvalidOperation, ROUND_DOWN

import yaml

from strategy import Strategy
from broker_setup import build_broker
from data_feed import load_history
from charting import render
from broker_lib import Order, OrderSide, BrokerAdapter

# ── 真實下單總開關(預設關閉,保護你的錢)──
ALLOW_LIVE_TRADING = {allow_live}


def _force_utf8_stdout() -> None:
    """讓獨立腳架在 Windows 非 UTF-8 管線也能輸出中文。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


DEFAULT_CONFIG = {{
    "market": {opts.market!r},
    "symbols": {opts.symbols!r},
    "broker": "paper",
    "paper": {{"starting_cash": 1_000_000, "fee_rate": 0.001}},
    "risk": {{"stop_loss_pct": 0.05, "take_profit_pct": 0.15,
              "max_position_pct": 0.2}},
    # 缺少設定檔或解析失敗時,明確退回未驗證且禁止真實下單。
    "anti_gambling": {{
        "stage_code": "unverified",
        "allow_live_trading": False,
    }},
}}


def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        # 沒有 config.yaml 時用內建預設(紙上模擬)
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        # 設定無法讀取或 YAML 解析失敗時絕不猜測,退回安全預設。
        print(
            f"⛔ {{path}} 無法安全解析({{type(exc).__name__}}),"
            "改用禁止真實下單的內建預設。"
        )
        return dict(DEFAULT_CONFIG)
    # 空檔或格式錯誤時 safe_load 回 None / 非 dict —— 直接用會 AttributeError。
    # 誠實退回內建預設(紙上模擬)並提醒,而不是丟 traceback。
    if not isinstance(data, dict):
        print(f"⚠️ {{path}} 是空的或格式不對,改用內建預設(紙上模擬)。")
        return dict(DEFAULT_CONFIG)
    return data


def maybe_enable_live(broker: BrokerAdapter, config: dict) -> None:
    """若使用者明確開啟真實下單,解除安全閘門;否則維持封鎖。

    所有彼此獨立的閘門都通過才會放行:
      1. main.py 的 ALLOW_LIVE_TRADING 常數(要手動改成 True)
      2. config.yaml 的 risk.i_have_read_disclaimer 設為 true
      3. anti_gambling.allow_live_trading 必須是布林值 true
      4. anti_gambling.stage_code 必須精確等於 tiny_live_validation
      5. 券商本身的 confirm_live_trading 雙重確認
    缺欄位、型別不符或其他階段一律 fail closed。
    """
    if not getattr(broker, "is_live", False):
        return  # 紙上模擬,無需解鎖

    if not ALLOW_LIVE_TRADING:
        raise SystemExit(
            "⛔ 偵測到真實券商,但 ALLOW_LIVE_TRADING 為 False。\\n"
            "   這是保護你的錢。確認策略已驗證、願意自負風險後,\\n"
            "   再把 main.py 的 ALLOW_LIVE_TRADING 改成 True。"
        )

    risk = config.get("risk") if isinstance(config, dict) else None
    if not isinstance(risk, dict) or risk.get("i_have_read_disclaimer") is not True:
        raise SystemExit(
            "⛔ 真實下單的第二道閘門未解除。\\n"
            "   請先閱讀免責聲明,並在 config.yaml 的 risk 區塊加上:\\n"
            "       i_have_read_disclaimer: true\\n"
            "   兩道閘門刻意分開,確保你不是只改了一個地方就誤觸真錢下單。"
        )

    anti_gambling = config.get("anti_gambling")
    if not isinstance(anti_gambling, dict):
        raise SystemExit(
            "⛔ 缺少有效的 anti_gambling 安全設定,真實下單維持封鎖。\\n"
            "   請重新執行含完整交易分析的 scaffold,不要手動猜測階段。"
        )

    if anti_gambling.get("allow_live_trading") is not True:
        raise SystemExit(
            "⛔ 交易分析尚未允許真實下單。\\n"
            "   anti_gambling.allow_live_trading 必須是布林值 true;"
            "缺少、false 或字串值一律封鎖。"
        )

    if anti_gambling.get("stage_code") != "tiny_live_validation":
        raise SystemExit(
            "⛔ 目前交易階段不允許真實下單。\\n"
            "   只有 stage_code: tiny_live_validation 才可能放行;"
            "缺少或其他階段一律維持紙上模擬。"
        )

    broker.confirm_live_trading(i_understand_the_risk=True)


def calculate_order_quantity(budget, price, market: str):
    """按市場保守計算數量；永遠不把不足一單位強制放大成 1。"""
    try:
        budget_d = Decimal(str(budget))
        price_d = Decimal(str(price))
    except (InvalidOperation, ValueError):
        return 0
    if not budget_d.is_finite() or not price_d.is_finite():
        return 0
    if budget_d <= 0 or price_d <= 0:
        return 0
    raw = budget_d / price_d
    if str(market).lower() == "crypto":
        # 先保守截到 8 位；交易所 adapter 仍會按即時 symbol 規格再次驗證。
        return float(raw.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN))
    return int(raw)


def reject_live_historical_replay(broker: BrokerAdapter) -> None:
    """內建 run() 是歷史/示範 replay，絕不允許它呼叫真實券商。"""
    if getattr(broker, "is_live", False):
        raise SystemExit(
            "⛔ main.py 的 run() 會重播歷史／示範 K 線，禁止連接真實券商。\\n"
            "   否則歷史訊號可能被一次送成多張真單。請另寫只處理最新已完成 K 線、\\n"
            "   且資料來源可驗證為即時的 live runner；不要解除這道閘門。"
        )


def run():
    _force_utf8_stdout()
    config = load_config()
    broker = build_broker(config)
    maybe_enable_live(broker, config)
    reject_live_historical_replay(broker)
    broker.connect()  # live broker 已在上一步退出；只有 paper 會走到這裡

    strategy = Strategy(config)
    symbols = config.get("symbols", {opts.symbols!r})
    market = str(config.get("market", {opts.market!r}))
    risk = config.get("risk", {{}})
    max_pct = risk.get("max_position_pct", 0.2)

    all_markers = []
    equity_curve = []
    candles_for_chart = []
    n_fills = 0
    chart_symbol = symbols[-1] if symbols else ""   # 圖表以最後一檔為例

    # 時間為外圈、標的為內圈。若按標的逐檔跑完整段歷史,前面標的的
    # 期末損益會被灌進後面標的的「歷史」權益曲線(時間穿越),圖會說謊。
    histories = {{sym: load_history(sym) for sym in symbols}}
    n_bars = min((len(h) for h in histories.values()), default=0)
    if chart_symbol:
        candles_for_chart = histories[chart_symbol][:n_bars]

    for i in range(n_bars):
        for symbol in symbols:
            history = histories[symbol]
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
                qty = calculate_order_quantity(budget, bar["close"], market)
                if qty <= 0:
                    continue  # 預算不足一單位就不下單，不可偷偷放大部位
                r = broker.place_order(Order(symbol, OrderSide.BUY, qty,
                                             client_tag=sig.reason))
                if r.ok:
                    n_fills += 1
                # 圖表標記只收「被繪製那一檔」的訊號 —— 其他標的的標記
                # 疊在別檔的 K 線上會畫錯位置,誤導判讀。
                if r.ok and symbol == chart_symbol:
                    all_markers.append({{"time": bar["time"], "price": bar["low"],
                                         "side": "buy", "text": sig.reason or "買"}})
            elif sig.action == "sell" and pos is not None:
                r = broker.place_order(Order(symbol, OrderSide.SELL, abs(pos.quantity),
                                             client_tag=sig.reason))
                if r.ok:
                    n_fills += 1
                if r.ok and symbol == chart_symbol:
                    all_markers.append({{"time": bar["time"], "price": bar["high"],
                                         "side": "sell", "text": sig.reason or "賣"}})

        # 每個時間點取樣一次「帳戶總權益」(時間軸對齊圖表主標的)——
        # 在內圈取樣會把同一時間戳寫入多次,或漏掉其他標的的損益貢獻
        if chart_symbol:
            equity_curve.append({{"time": histories[chart_symbol][i]["time"],
                                  "value": broker.get_account().equity}})

    acct = broker.get_account()
    print("=" * 50)
    # 用實際 broker 實例的名稱:config 寫 shioaji 但 adapter 還沒解註解時,
    # 實際跑的是 PaperBroker —— 印 config 值會誤導使用者以為單已送到券商
    print(f"  策略執行完畢（{{getattr(broker, 'name', '?')}} 模式）")
    print(f"  最終權益: {{acct.equity:,.2f}}")
    print(f"  成交筆數: {{n_fills}}")
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
