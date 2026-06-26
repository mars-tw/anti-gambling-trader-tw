# 反詐投資王（Anti-Gambling Trader）

> 一個**誠實到不討喜**的交易績效分析工具。
> 它不會告訴你「你會賺錢」，而是用統計學告訴你：
> 你過去的獲利，究竟是**可重複的優勢（edge）**，還是**運氣 + 倖存者偏差（賭博）**。

支援 **台股 / 美股 / 加密貨幣**，匯入 **CSV / JSON / Excel** 的交易紀錄，自動：

- 計算 **勝率、盈虧比、期望值、獲利因子、最大回撤、夏普 / 索提諾值**
- 用 **統計顯著性檢定（t 檢定 + Bootstrap）** 判斷「這是優勢還是運氣」
- 用 **樣本外驗證** 揭穿過度配適與倖存者偏差
- 掃描 **賭博特徵警訊**（賺小賠大、獲利過度集中、長連虧、純當沖…）
- 反推你的交易邏輯，產生 **可回測的策略骨架**（backtrader / vectorbt / 通用）
- 若判定**不適合長期投資，明確勸退**

## 為什麼做這個

絕大多數散戶虧錢，是因為把「運氣好」誤當成「有本事」。
一段帳面獲利，在統計上經常和「純運氣」無法區分。這個工具的唯一目的，
就是在你賠掉更多錢之前，**用數學說真話**。

## 安裝

核心引擎是**純 Python 標準函式庫，零外部依賴**。

```bash
git clone <repo>
cd 反詐投資王
# 只有要讀 Excel 才需要：
pip install openpyxl
# 只有要實際跑回測骨架才需要：
pip install backtrader   # 或 vectorbt
```

需求：Python 3.10+

## 快速開始

```bash
# 分析賭博型範例（會被勸退，exit code 2）
python -m core.cli analyze examples/tw_stock_gambling.csv --market tw_stock

# 分析具優勢範例（通過驗證，exit code 0）
python -m core.cli analyze examples/us_stock_edge.csv --market us_stock

# 同時輸出 JSON 結果與策略骨架
python -m core.cli analyze examples/us_stock_edge.csv \
    --json result.json --strategy my_strategy.py --framework vectorbt
```

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
