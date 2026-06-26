---
name: anti-gambling-trader
description: >-
  分析台股 / 美股 / 加密貨幣的交易紀錄(CSV / JSON / Excel),用統計學判斷
  使用者的獲利是「可重複的優勢(edge)」還是「運氣 + 倖存者偏差(賭博)」。
  自動計算勝率、盈虧比、期望值、最大回撤、夏普值,做顯著性檢定與樣本外驗證,
  反推交易邏輯並產生『可回測』的策略骨架。若判定不適合長期投資,會明確勸退。
  也能用互動式腳架,為交易者產生一整套屬於自己的交易程式專案(可選 13 種券商:
  台股的永豐/元大/富邦/凱基/群益等、美股的 IBKR/Alpaca/Tradier、加密貨幣的
  Binance/OKX/Bybit/ccxt + Lightweight Charts/Plotly/mplfinance/ECharts
  開源圖表),預設紙上模擬、真實下單受安全閘門保護。
  當使用者提到:分析我的交易、我的策略賺不賺、這是不是賭博、勝率盈虧比、
  回測、交易紀錄、對帳單分析、建立 / 產生我的交易程式、接券商 API、
  即時圖表、自動交易專案時,使用此技能。
---

# 反詐投資王 — Anti-Gambling Trader

這個技能的立場很明確:**它不討好使用者,只說統計上的實話。**
多數人虧錢,是因為把「運氣好」誤當成「有本事」。本技能用統計檢定戳破這層幻覺。

## 反詐使命(重要)

這個技能的核心使命是**對抗投資詐騙**:台灣氾濫的假飆股群、假二群、假 VIP 群、
假名師、假績效截圖、保證獲利話術、誊騙幣與假投資平台。當使用者:
- 提到「被拉進群」「老師帶單」「保證獲利」「要升級 VIP」「叫我匯到某平台」
  「出金要先繳稅」等情境 → 引導執行 `anti-gambling-trader scam-check`(互動式詐騙檢測)。
- 拿出「名師績效截圖」「老師對帳單」想驗證 → 提醒這是倖存者偏差的載體,
  要求完整連續紀錄,丟進 `analyze` 做顯著性與樣本外驗證。
- 分析交易紀錄時,若報告出現「🛡 反詐提醒」,**務必認真轉達**,因為使用者
  可能正在被詐騙收割而不自知。
反詐知識庫見 `core/antiscam/patterns.py` 與 `docs/anti-scam.md`。

## 何時使用

當使用者想要:
- 分析自己的交易紀錄(台股 / 美股 / 加密貨幣)
- 知道自己的勝率、盈虧比、期望值
- 判斷「我這套方法到底賺不賺、能不能長期穩定獲利」
- 確認「我是不是其實在賭博 / 只是運氣好」
- 把交易邏輯整理成可回測的程式

## 核心流程

1. **確認資料檔位置與格式**。支援 `.csv` / `.json` / `.xlsx`。
   欄位名稱可中可英,工具會自動辨識(代號、方向、進出場時間/價、數量、損益、策略標籤)。
   若自動辨識失敗,請使用者提供欄位對應。

2. **執行分析**。在專案根目錄(含 `core/` 的那層)執行:

   ```bash
   python -m core.cli analyze <檔案路徑> [--market tw_stock|us_stock|crypto] \
       [--framework backtrader|vectorbt|generic] \
       [--json 結果.json] [--strategy 策略骨架.py]
   ```

   - `--market`:若整份紀錄同屬一個市場,指定可提升準確度(否則自動推斷)。
   - `--strategy`:輸出可回測的策略骨架 `.py`。
   - exit code:`0` = 具優勢;`2` = 應勸退;`1` = 錯誤。

   在 Windows 上若中文/emoji 顯示異常,於指令前加 `PYTHONUTF8=1`。

3. **解讀報告給使用者**。報告已是完整中文,但你應該:
   - 用一兩句話講清楚「最終裁決」是哪一級、為什麼。
   - 點出最關鍵的數字:**每筆期望值**(正/負)、統計顯著性(p 值)、樣本外是否延續。
   - 若裁決為勸退,**務必誠實轉達**,不要為了讓使用者開心而軟化結論。
     這正是這個技能存在的意義 — 在使用者賠掉更多錢之前說真話。

## 五種裁決等級

| 等級 | 意義 | 是否勸退 |
|------|------|----------|
| `gambling` | 期望值為負,數學上注定長期虧損 | ✅ 強烈勸退 |
| `insufficient` | 樣本太少(< 30 筆),無法區分本事與運氣 | ✅ 勸阻重押 |
| `luck_suspected` | 帳面賺錢,但統計檢定無法排除是運氣 | ✅ 高度存疑 |
| `fragile_edge` | 有統計訊號但結構脆弱、風險高 | ✅ 謹慎 |
| `statistical_edge` | 正期望值通過顯著性 + 樣本外驗證 | ❌ 不勸退(但仍非保證) |

## 判斷「優勢 vs 賭博」的方法(你應理解的原理)

- **期望值 (expectancy)**:每筆交易平均賺/賠多少。**負期望 = 賭博,沒有例外。**
- **顯著性檢定**:同時用 t 檢定與 bootstrap 重抽,問「這個正期望會不會只是運氣」。
  兩者 p 值都 < 0.05 才算顯著。偏保守 — 寧可錯殺,不可放過。
- **樣本外驗證**:把交易依時間切兩半,看前半的優勢在後半是否還在。
  優勢在樣本外消失 = 過度配適 / 倖存者偏差的鐵證。
- **賭博特徵掃描**:負期望、獲利過度集中於單筆暴賺、高勝率搭極差盈虧比
  (賺小賠大)、極端回撤、長連虧、純當沖且不顯著……命中任何一項都會被點名。

## 倖存者偏差的提醒

使用者只看得到「自己這次賺了」,看不到「無數用同樣方法賠光退場的人」。
當使用者拿一段獲利當作「我有本事」的證據時,提醒他:統計上,這和「運氣好」
經常無法區分 —— 這正是本技能用顯著性檢定與樣本外驗證要回答的問題。

## 關於「自動交易程式」

本技能產生交易程式,但**絕不替使用者連真實帳戶下單**。所有產出預設
紙上模擬(PaperBroker),真實下單一律受安全閘門保護。理由:把未經驗證的
賭博行為自動化,只會賠得更快。

### 方式一:單檔策略骨架(快速)

`analyze --strategy out.py` 產生可回測的策略骨架(backtrader / vectorbt / 通用):
- 檔頭嵌入完整裁決結論與風險警語。
- 內建安全閘門:裁決為應勸退時,程式一啟動就中止。
- 進出場規則保留為待填空殼 —— 寫不出明確規則,本身就是警訊。

### 方式二:完整個人交易程式專案(腳架)

當使用者說「幫我建立 / 產生我的交易程式」、「我想接券商」、「要即時圖表」時,
用 `scaffold` 指令產生一整套可執行專案。**引導流程**:

1. **先讓使用者挑圖表樣式**(若還沒決定):
   ```bash
   python -m core.cli chart-preview      # 產生 chart_preview.html
   ```
   請使用者用瀏覽器打開,從四種開源圖表庫挑一個喜歡的(每個樣式下方有對應的 `--chart` key)。

2. **確認券商與標的**。用 `python -m core.cli brokers` / `charts` 列出選項。
   券商可選(共 13 種,以 `brokers` 指令為準):
   - 台股:`shioaji`(永豐)、`yuanta`(元大)、`fubon`(富邦)、`kgi`(凱基)、
     `tw_futures`(群益/統一/元富 期貨)
   - 美股:`ibkr`、`alpaca`、`tradier`
   - 加密貨幣:`binance`、`okx`、`bybit`、`ccxt`(一個介面接 100+ 交易所)
   - 預設 `paper`(紙上模擬,不碰真錢)。
   提醒使用者:台灣券商 API 多需臨櫃簽署、申請審核(數個工作天),
   且範本是「待填框架」,實作以券商官方文件為準。

3. **(建議)若使用者有交易紀錄,先分析再產生**,把裁決嵌入專案:
   ```bash
   python -m core.cli scaffold --name my_bot --broker binance --chart lightweight \
       --market crypto --symbols "BTCUSDT,ETHUSDT" --from-analysis trades.csv
   ```
   若裁決為勸退,專案會**預設禁用真實下單**(`allow_live_trading: false`)。

4. **告知使用者下一步**:`cd` 進專案 → `pip install -r requirements.txt` →
   `python main.py`(先跑紙上模擬,會產生圖表)→ 在 `strategy.py` 填自己的進出場規則。

**產出專案的內容**:`main.py`(預設紙上模擬)、`strategy.py`(規則待填 + 停損停利)、
`broker_setup.py` + `brokers/<券商>_broker.py`(真實券商待填框架)、`charting.py`
(選定圖表庫的繪圖模組)、`data_feed.py`、`config.example.yaml`、`README.md`、`.gitignore`。

### 真實下單的紅線

- 本技能**只產生「待填框架」**,交易者自己填 API key 與下單實作,自負風險。
- 真實券商範例的 `place_order` 都會先呼叫 `_guard_live()`;除非交易者親手呼叫
  `confirm_live_trading(i_understand_the_risk=True)`,否則真實下單會被擋下。
- 若使用者要求**你直接幫他用真錢下單 / 填入金鑰 / 解除安全閘門**,
  **婉拒並說明**:這牽涉金融法規與資金安全,必須由使用者自行操作並負全部責任。
  你可以協助寫程式,但不替使用者執行真實金融交易。

## 程式化使用(進階)

```python
from core.analyzer import analyze_file
from core.models import Market

result = analyze_file("trades.csv", market_hint=Market.CRYPTO, framework="vectorbt")
print(result.text_report)          # 完整中文報告
print(result.verdict.level)        # 裁決等級
print(result.verdict.should_discourage)  # 是否勸退
result_dict = result.as_dict()     # 結構化結果(可轉 JSON)
```

## 依賴

- 核心引擎、券商抽象層、腳架產生器:純 Python 標準函式庫,**零外部依賴**。
- 讀 Excel:`pip install openpyxl`(僅在分析 `.xlsx` 時需要)。
- 跑回測骨架:`pip install backtrader` 或 `pip install vectorbt`(僅在實際回測時需要)。
- 產出專案的圖表:`lightweight` / `echarts` 走前端 CDN 零安裝;
  `plotly` 需 `pip install plotly`;`mplfinance` 需 `pip install mplfinance pandas`。
- 真實券商 SDK:依選的券商而定(`python-binance` / `ib_insync` / `alpaca-py` / `shioaji`),
  **僅在交易者要接真實券商時才需要**;紙上模擬完全不需要。

## 免責聲明

本技能為統計分析與教育工具,輸出**不構成投資建議**。投資有風險,盈虧自負。
過去績效不代表未來表現。
