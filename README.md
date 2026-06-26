# 反詐投資王（Anti-Gambling Trader）

> 一個**誠實到不討喜**的交易績效分析工具。
> 它不會告訴你「你會賺錢」，而是用統計學告訴你：
> 你過去的獲利，究竟是**可重複的優勢（edge）**，還是**運氣 + 倖存者偏差（賭博）**。

## 👶 完全沒用過電腦命令？從這裡開始

如果你沒裝過 Python、沒用過終端機、也沒用過 AI，請看
**[新手快速上手指南 docs/quickstart.md](docs/quickstart.md)** ——
它從「怎麼裝 Python、怎麼打開終端機」開始，一步一步帶你在 15 分鐘內上手，
還教你怎麼用 AI（Claude Code）用講話的方式操作這個工具。

## 🛡 這個工具的反詐使命

台灣到處都是**假飆股群、假二群、假 VIP 群、假名師、假績效截圖、保證獲利話術、
詐騙幣與假投資平台**。它們全都靠同一招：用**倖存者偏差**與**精選截圖**，
讓你誤以為有穩賺的捷徑。

這個工具叫「反詐投資王」不是叫假的 —— 它存在的真正原因，就是**用統計與數學
拆穿這些騙局**。詐騙最怕的，就是你冷靜地把它的承諾丟進數學裡檢驗。

```bash
anti-gambling-trader scam-check     # 互動式檢測：你是不是遇到投資詐騙？
```

詳見 **[反詐指南 docs/anti-scam.md](docs/anti-scam.md)**。

---

支援 **台股 / 美股 / 加密貨幣**，匯入 **CSV / JSON / Excel** 的交易紀錄，自動：

- 計算 **勝率、盈虧比、期望值、獲利因子、最大回撤、夏普 / 索提諾值**
- 用 **統計顯著性檢定（t 檢定 + Bootstrap）** 判斷「這是優勢還是運氣」
- 用 **樣本外驗證** 揭穿過度配適與倖存者偏差
- 掃描 **賭博特徵警訊**（賺小賠大、獲利過度集中、長連虧、純當沖…）
- **逐策略體檢**：對你的每一招（突破 / 抄底 / 聽明牌…）各下一次裁決，揪出**哪一招在送錢**
- **反事實分析**：算出「停掉最差那一招，整體會變怎樣」
- **轉正數字**：告訴你「勝率要到幾 % / 盈虧比要拉到多少，期望值才會轉正」
- **反詐偵測**：把「聽老師 / 跟單」的交易單獨抽出算期望值，用你自己的錢證明跟單必賠
- 反推你的交易邏輯，產生 **可回測的策略骨架**（backtrader / vectorbt / 通用）
- 若判定**不適合長期投資，明確勸退**

## 為什麼做這個

絕大多數散戶虧錢，是因為把「運氣好」誤當成「有本事」——
而詐騙集團正是利用這個認知弱點，用假群組、假名師、假截圖收割你。
一段帳面獲利，在統計上經常和「純運氣」無法區分。這個工具的唯一目的，
就是在你賠掉更多錢、或被詐騙收割之前，**用數學說真話**。

## 安裝

核心引擎是**純 Python 標準函式庫，零外部依賴**。

```bash
git clone https://github.com/mars-tw/anti-gambling-trader-tw.git
cd anti-gambling-trader-tw
pip install -e .            # 安裝本體（之後可用 anti-gambling-trader 指令）

# 以下為可選依賴：
# pip install openpyxl       # 只有要讀 Excel (.xlsx) 才需要
# pip install backtrader     # 只有要實際跑回測骨架才需要（或 vectorbt）
```

需求：Python 3.10+。所有指令請在專案根目錄（含 `core/` 的那層）執行；
macOS / Linux 若 `python` 指到 Python 2，請改用 `python3`。

## 快速開始（30 秒上手）

```bash
# 0. 還沒有自己的資料？一行指令立刻看效果：
python -m core.cli demo               # 看「賭博型」範例
python -m core.cli demo --edge        # 看「具優勢」範例

# 1. 不知道資料格式？產生一份空白範本照填：
python -m core.cli init-template      # 產生 trades_template.csv

# 2. 分析你自己的資料（市場會自動推斷）：
python -m core.cli analyze 你的交易.csv

# 3. 欄位自動辨識失敗？手動指定對應：
python -m core.cli analyze 你的交易.csv --field symbol=代號 --field entry_price=買價

# 進階：輸出 JSON 結果與策略骨架
python -m core.cli analyze --example us --json result.json --strategy my_strategy.py
```

裝好本體後，上面的 `python -m core.cli` 都可換成更短的 `anti-gambling-trader`。

> Windows 終端機若中文 / emoji 顯示異常，指令前加 `PYTHONUTF8=1`。

## 輸入格式

欄位名稱**中英皆可、會自動辨識**。最少需要能算出每筆損益的資訊：
（代號 + 進場價 + 出場價 + 數量）或（代號 + 損益）。

| 標準欄位 | 可接受的欄名（部分範例） | 必要性 |
|----------|--------------------------|--------|
| symbol | 代號 / ticker / 股票代號 / pair | 必要 |
| side | 方向 / 買賣 / side / long_short | 選填（預設做多） |
| entry_time / exit_time | 進場時間 / 出場時間 / open_time | 建議 |
| entry_price / exit_price | 進場價 / 出場價 / 買價 / 賣價 | 與 pnl 二擇一 |
| quantity | 數量 / 股數 / 張數 / qty | 與 pnl 二擇一 |
| fees | 手續費 / 費用 / commission | 選填（未填則自動估算） |
| pnl | 損益 / 盈虧 / 已實現損益 / profit | 與價格二擇一 |
| tag | 策略 / strategy / 進場理由 | 選填（強烈建議） |

> **建議務必填 `tag`（策略標籤）**：工具會分別計算每套邏輯的勝率，
> 幫你看出「到底是哪一招真的有效、哪一招只是在送錢」。

## 五種裁決等級

| 等級 | 意義 | 勸退 |
|------|------|------|
| 🟥 `gambling` | 期望值為負，數學上注定長期虧損 | ✅ 強烈勸退 |
| 🟧 `insufficient` | 樣本太少，無法區分本事與運氣 | ✅ 勸阻重押 |
| 🟨 `luck_suspected` | 帳面賺錢，但統計上像運氣 | ✅ 高度存疑 |
| 🟨 `fragile_edge` | 有統計訊號但結構脆弱、風險高 | ✅ 謹慎 |
| 🟩 `statistical_edge` | 正期望值通過顯著性 + 樣本外驗證 | ❌ 不勸退（仍非保證） |

## 它如何分辨「優勢」與「賭博」

1. **期望值**：每筆平均賺/賠多少。**負期望 = 賭博，沒有例外。**
2. **顯著性檢定**：t 檢定 + Bootstrap 重抽，雙雙 p < 0.05 才算「不是運氣」。偏保守。
3. **樣本外驗證**：交易依時間切兩半，前半的優勢在後半若消失 → 過度配適 / 倖存者偏差。
4. **賭博特徵掃描**：負期望、單筆暴賺撐場、賺小賠大、極端回撤、長連虧、純當沖…

## 建立你自己的交易程式

除了單檔策略骨架，本工具還能用**互動式腳架**，為你產生一整套可執行的個人交易程式專案。

```bash
# 1. 先挑圖表樣式（四種開源圖表庫並排預覽）
python -m core.cli chart-preview          # 產生 chart_preview.html，用瀏覽器打開挑選

# 2. 看看有哪些券商 / 圖表可選
python -m core.cli brokers
python -m core.cli charts

# 3. 產生專案（建議帶 --from-analysis 先驗證，把裁決嵌入專案）
python -m core.cli scaffold --name my_bot --broker binance --chart lightweight \
    --market crypto --symbols "BTCUSDT,ETHUSDT" --from-analysis my_trades.csv

# 4. 跑起來（預設紙上模擬，不碰真錢）
cd my_bot && pip install -r requirements.txt && python main.py
```

**可選券商**（你自己接入 API，填 key 與下單實作）：

| key | 券商 | 市場 |
|-----|------|------|
| `paper` | 紙上模擬（預設，完整可用、不碰真錢） | 全 |
| `binance` | Binance | 加密貨幣 |
| `ibkr` | Interactive Brokers | 美股 / 全球 |
| `alpaca` | Alpaca | 美股 |
| `shioaji` | 永豐 Shioaji | 台股 |

**可選開源圖表庫**：`lightweight`（TradingView，Apache-2.0）、`plotly`（MIT）、
`mplfinance`（BSD）、`echarts`（Apache-2.0）。

### 三層安全設計（保護你的錢）

1. **預設紙上模擬**：產出專案預設用 `PaperBroker`，完整模擬撮合但不碰真錢。
2. **真實下單安全閘門**：真實券商的下單會被攔截，除非你親手呼叫
   `confirm_live_trading(i_understand_the_risk=True)`，且 `main.py` 的
   `ALLOW_LIVE_TRADING` 改為 `True`。
3. **裁決連動**：若你的交易紀錄被判定為賭博，產出專案會**預設禁用真實下單**。

> 本工具產生程式碼協助你，但**絕不替你用真錢下單、不替你填金鑰、不替你解除安全閘門**。
> 真實金融交易必須由你自己操作並負全部責任。把未經驗證的賭博自動化，只會賠得更快。

## 專案結構

```
core/
  models.py            # 統一資料模型（Trade / TradeLog）
  analyzer.py          # 高階一行式進入點
  cli.py               # 命令列介面
  report.py            # 中文報告產生器
  ingest/              # 匯入層：CSV/JSON/Excel 自動辨識 + 三市場成本模型 + API 連接器
  metrics/             # 績效指標計算
  verdict/             # 統計裁決引擎（反賭博核心）+ 顯著性檢定
  strategy/            # 交易模式反推 + 策略骨架產生
  backtest/            # 樣本外驗證
  broker/              # 券商下單抽象層 + PaperBroker + 真實券商範例框架
  charts/              # 四種開源圖表庫範本 + 樣式預覽介面
  scaffold/            # 互動式個人交易程式專案產生器
.claude/skills/anti-gambling-trader/SKILL.md   # Claude Code 技能包裝
examples/              # 三市場範例資料
tests/                 # 單元測試（test_core.py + test_trading_tools.py）
```

## 測試

```bash
python tests/test_core.py        # 內建簡易執行器，無需 pytest
# 或
python -m pytest tests/ -v
```

## 作者

好棒棒反詐協會 - 免費顧問 阿軒割割

## 授權

MIT License — 開源、自由使用。詳見 [LICENSE](LICENSE)。

## ⚠️ 免責聲明

本工具為**統計分析與教育用途**，輸出**不構成任何投資建議**。
投資有風險，盈虧自負。過去績效不代表未來表現。
作者不對任何人依本工具做出的決策與後果負責。

完整免責聲明請見 [DISCLAIMER.md](DISCLAIMER.md)。
