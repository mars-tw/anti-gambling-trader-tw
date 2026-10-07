# 反詐投資王（Anti-Gambling Trader）

**你在懷疑「投資群組是不是詐騙」「老師帶單可不可信」「我的交易到底是實力還是運氣」嗎？**
這是一個免費開源的交易統計、投資反詐與自動化交易開發工具：把交易紀錄
（台股／美股／加密貨幣）丟進來，
它用期望值、顯著性檢定與樣本外驗證，給你統計證據判斷獲利更像**可重複的優勢**，
還是**運氣＋倖存者偏差（賭博）**；並內建詐騙話術掃描、假績效鑑識與假老師檢驗。
它也能把交易邏輯轉成可回測策略，並產生可接券商 API 的自動化交易程式專案。
直接在終端機執行本專案的本機 CLI 時，交易紀錄由本機讀取與分析，程式本身不會把
紀錄上傳。若你把檔案、截圖或工具輸出交給 Claude Code／其他雲端 AI，內容可能依
該供應商政策傳送與保存；分享前請遮蔽姓名、帳號、LINE ID 與可識別資訊。參考
[Claude Code 官方資料使用說明](https://code.claude.com/docs/en/data-usage)。此外，部分生成
圖表會載入外部 CDN；自行啟用券商/API 路徑則會明確連線到相應外部服務。

> 一個**誠實到不討喜**的工具。它不會告訴你「你會賺錢」——
> 不適合長期投資的，它會直接勸退你。
> 👉 急著找答案？先看 **[常見問題 FAQ](docs/faq.md)**：投資群組是詐騙嗎、
> 勝率 90% 可信嗎、出金要先繳稅正常嗎、被騙了怎麼辦。

> 🤖 **不只分析，也能建立自動化交易程式。** `scaffold` 會產生自包含的策略、
> 風控、券商介面、資料源與圖表專案，提供 `PaperBroker` 與 13 種真實券商／交易所範本。
> 紙上模擬可直接執行；自行實作真實自動交易時，必須接妥券商 API、即時行情 runner，
> 並沿用四層安全檢查。預設生成的歷史 runner 不會送出真單；只有使用者自行填入金鑰、
> 完成 live runner 並明確解鎖後，live adapter 才可能送單。

**English TL;DR** — An honest, open-source (MIT) trading-performance analyzer for
Taiwan securities and derivatives, US stocks, crypto and forex. It uses expectancy, significance tests (t-test +
centered bootstrap) and out-of-sample validation to tell whether your P&L is a
repeatable edge or survivorship-biased luck — and it will actively discourage you
if it's the latter. Includes scam-language scanning, fake-performance forensics
and a guru-claim probability checker. The statistical core uses the Python standard library,
and trade-record analysis stays local.
It can also generate backtestable strategy skeletons and automated-trading projects with
paper trading by default and explicit safety gates for any user-built live connection.

## 👶 完全沒用過電腦命令？從這裡開始

如果你沒裝過 Python、沒用過終端機、也沒用過 AI，請看
**[新手快速上手指南 docs/quickstart.md](docs/quickstart.md)** ——
它從「怎麼裝 Python、怎麼打開終端機」開始，一步一步帶你上手。安裝後先打：

```bash
anti-gambling-trader start
```

畫面會依「完整紀錄、單筆交易、交易截圖，還是 LINE 對話」列出最短命令。
沒有試算表也能用 `record` 的五個短問題記一筆交易。

## 🛡 這個工具的反詐使命

台灣到處都是**假飆股群、假二群、假 VIP 群、假名師、假績效截圖、保證獲利話術、
詐騙幣與假投資平台**。它們絕大多數靠同一招：用**倖存者偏差**與**精選截圖**，
讓你誤以為有穩賺的捷徑。

這個工具叫「反詐投資王」不是叫假的 —— 它存在的真正原因，就是**用統計與數學
檢驗這些承諾**。詐騙最怕的，就是你冷靜地把它的承諾丟進數學裡檢驗。

```bash
# 貼上群組對話，掃描詐騙話術（不給假百分比，只給風險等級）
anti-gambling-trader scan-text "老師帶單保證獲利，快加VIP客服"

# 檢驗老師的宣稱：「勝率90%、月報酬20%」純靠運氣出現的機率是多少？
anti-gambling-trader guru-check --win-rate 0.9 --trades 10 --monthly-return 0.2

# 用數學算出「連贏10次的神人」有多容易靠運氣出現
anti-gambling-trader survivorship

# 鑑識老師/平台宣稱的報酬序列是否有可疑徵兆（過度平滑、高得可疑的夏普）
anti-gambling-trader forensics --file 老師的月報酬.txt

# 互動式自我檢測
anti-gambling-trader scam-check
```

詳見 **[完整功能與判讀指南](docs/user-guide.md)**、
**[反詐指南](docs/anti-scam.md)** 與 **[FAQ](docs/faq.md)**。

> 🆘 **懷疑自己正在被詐騙？** 立刻停止匯款，撥打 **165 反詐騙專線**，
> 或上 [165 全民防騙網](https://165.npa.gov.tw)。查證合法業者：
> [金管會證期局](https://www.sfb.gov.tw)；證券期貨爭議求助：
> [投保中心](https://www.sfipc.org.tw)。（官方連結查核日期：2026-07）
> 本工具是統計輔助，**不是司法鑑定**；官方管道永遠優先。

---

支援 **台股 / 台股 ETF / 台指期與選擇權 / 美股 / 加密貨幣 / 外匯**，
匯入 **CSV / JSON / Excel** 的交易紀錄，自動：

- 計算 **勝率、盈虧比、期望值、獲利因子、最大回撤、夏普 / 索提諾值**
- 用 **統計顯著性檢定（t 檢定 + Bootstrap）** 判斷「這是優勢還是運氣」
- 用 **樣本外驗證** 揭穿過度配適與倖存者偏差
- 掃描 **賭博特徵警訊**（賺小賠大、獲利過度集中、長連虧、純當沖…）
- **逐策略體檢**：對你的每一招（突破 / 抄底 / 聽明牌…）呈現描述統計，揪出**哪一招在送錢**
  （刻意不對個別策略做優勢認證 —— 多重比較未校正會把運氣誤認成優勢，詳見方法論）
- **反事實分析**：算出「停掉最差那一招，整體會變怎樣」
- **轉正數字**：告訴你「勝率要到幾 % / 盈虧比要拉到多少，期望值才會轉正」
- **反詐偵測**：把「聽老師 / 跟單」的交易單獨抽出算期望值 —— 用你自己的數字檢驗跟單到底賺不賺
- **風險情境模擬**：用你的損益分布模擬未來，看有多少比例的路徑會爆倉
- **時間趨勢**：月／季描述統計，以及固定早期與近期切點的單一優勢衰減檢定
- **LINE 對話掃描**：理解 LINE 匯出標頭與跨訊息話術，顯示發言者、時間、原句與理由
- **交易截圖覆核**：OCR 擷取進出場點位、數量、時間、損益、停損/停利；低信心不自動填
- **交易技術線索**：辨識突破、均線、RSI、MACD 等畫面文字，但不把線索冒充成策略優勢
- **新手階段分流**：用完整交易紀錄判斷目前只適合停手、紙上模擬或極小額驗證
- **HTML 報告 + 分享圖卡**：可存檔、可截圖傳給家人的白紙黑字數據
- 反推你的交易邏輯，產生 **可回測的策略骨架**（backtrader / vectorbt / 通用）
- **自動化交易程式腳架**：產生自包含的策略、風控、券商介面、資料源與圖表專案；
  `PaperBroker` 可直接執行，另有 13 種真實券商／交易所範本供使用者自行接線與驗證
- 若判定**不適合長期投資，明確勸退**

支援市場：台股、台股 ETF、**台指期 / 選擇權（含契約乘數）**、美股、加密貨幣、**外匯**。

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
# pip install -e ".[screenshot]"  # 圖片 OCR；另需安裝 Tesseract 與繁中字庫
# pip install backtrader     # 只有要實際跑回測骨架才需要（或 vectorbt）
```

需求：Python 3.10+。所有指令請在專案根目錄（含 `core/` 的那層）執行；
macOS / Linux 若 `python` 指到 Python 2，請改用 `python3`。

## 快速開始（30 秒上手）

```bash
# 0. 第一次使用：依你手上的資料顯示最短路徑
python -m core.cli start

# 1. 沒有表格也能逐筆記錄（五個短問題）
python -m core.cli record

# 2. 用完整紀錄快速判斷目前適合哪個階段
python -m core.cli fit-check my_trades.csv

# 3. 掃描 LINE 對話紀錄
python -m core.cli scan-text --file LINE對話.txt

# 4. 辨識交易截圖；低信心欄位一定要求人工確認
python -m core.cli scan-screenshot 券商截圖.png

# 5. 還沒有自己的資料？一行指令立刻看效果：
python -m core.cli demo               # 看「賭博型」範例
python -m core.cli demo --edge        # 看「具優勢」範例

# 6. 想用 Excel？產生範本；最簡單填代號、平倉日、淨損益、損益幣別、策略
python -m core.cli init-template      # 產生 trades_template.csv

# 7. 查看完整統計報告（市場會自動推斷）：
python -m core.cli analyze 你的交易.csv

# 8. 欄位自動辨識失敗？手動指定對應：
python -m core.cli analyze 你的交易.csv --field symbol=代號 --field entry_price=買價

# 進階：輸出 JSON 結果與策略骨架
python -m core.cli analyze --example us --json result.json --strategy my_strategy.py
```

裝好本體後，上面的 `python -m core.cli` 都可換成更短的 `anti-gambling-trader`。

> 截圖辨識只擷取「候選欄位」與文字線索，不會自動存入交易紀錄。OCR 可能看錯
> 正負號、小數點與買賣方向，必須逐欄對照原圖；單張截圖也不能證明績效或交易能力。

每條操作路徑、判讀門檻與人工覆核清單，見[完整功能與判讀指南](docs/user-guide.md)。

> Windows 終端機若中文 / emoji 顯示異常：PowerShell 先執行 `$env:PYTHONUTF8=1` 再跑指令；
> macOS / Linux 則在指令前面加 `PYTHONUTF8=1`。

## 輸入格式

欄位名稱**中英皆可、會自動辨識**。最少需要能算出每筆損益的資訊：
（代號 + 方向 + 進場價 + 出場價 + 數量）或
（代號 + 已扣全部成本的淨損益 + 帳戶結算幣別）。

| 標準欄位 | 可接受的欄名（部分範例） | 必要性 |
|----------|--------------------------|--------|
| symbol | 代號 / ticker / 股票代號 / pair | 必要 |
| side | 方向 / 買賣 / side / long_short | 價量推算必填；direct pnl 可缺但不判方向偏好 |
| entry_time / exit_time | 進場時間 / 出場時間 / open_time | 建議 |
| entry_price / exit_price | 進場價 / 出場價 / 買價 / 賣價 | 與 pnl 二擇一 |
| quantity | 數量 / 股數 / 張數 / qty | 與 pnl 二擇一 |
| fees | 交易成本 / total_fee，或完整手續費＋稅 | 價量推算時未填才估算；direct net pnl 不重複扣 |
| pnl | pnl / 損益 / net_pnl / 淨損益 | 與價格二擇一；標準欄位契約為已扣所有成本的淨額 |
| pnl_currency | 損益幣別 / 帳戶幣別 | direct pnl 必填；價量推算才可依商品原生幣別判定 |
| tag | 策略 / strategy / 進場理由 | 選填（強烈建議） |

### 資料不完整時的保守處理

- 缺少、無效或全部相同的平倉時間時，不用列順序冒充時序，也不宣稱完成樣本外驗證。
- 不同或不明損益幣別不直接相加；混用有時區與無時區時間不安全排序。
- 契約乘數、名目本金或 direct P&L 的進場基準不可靠時，停用報酬率與回撤百分比。
- 模糊 gross/net 欄位不冒充淨損益；可信 direct net P&L 可保留，但相依指標會拒算。

完整規則與修正方式見[完整功能與判讀指南：Fail closed](docs/user-guide.md#4-資料不完整時工具如何-fail-closed)。

> `盈虧`、`已實現損益`、`realized_pnl` 的 gross/net 語意不明，工具會要求改成
> 明示「淨」的欄名，或用 `--field pnl=你的欄名` 親自確認。`profit` 等 gross 欄
> 只有在同列提供可信的 `total_fee/交易成本` 時才會扣成本後載入。

> **建議務必填 `tag`（策略標籤）**：工具會分別呈現每套邏輯的描述統計，
> 幫你找出「樣本裡哪一招較差、值得停用檢查」；它不會認證個別策略具有優勢。

## 五種裁決等級

| 等級 | 意義 | 勸退 |
|------|------|------|
| 🟥 `gambling` | 樣本期望值為負 —— 方法不變，長期的統計預期就是虧損 | ✅ 強烈勸退 |
| 🟧 `insufficient` | 樣本太少，無法區分本事與運氣 | ✅ 勸阻重押 |
| 🟨 `luck_suspected` | 帳面賺錢，但統計上像運氣 | ✅ 高度存疑 |
| 🟨 `fragile_edge` | 有統計訊號但結構脆弱、風險高 | ✅ 謹慎 |
| 🟩 `statistical_edge` | 正期望值通過顯著性檢定（樣本外驗證另行報告） | ❌ 不勸退（仍非保證） |

## 它如何分辨「優勢」與「賭博」

1. **期望值**：每筆平均賺/賠多少。**真實期望值為負，長期就是輸（數學定義）；**
   **樣本期望值為負則「先當賭博處理」（保守原則）—— 它是估計，所以裁決同時附不確定性檢定。**
2. **顯著性檢定**：t 檢定 + 置中 Bootstrap（shift method）重抽；雙雙 p < 0.05
   是在指定虛無假設與模型成立時，反對「平均損益不為正」的證據。p 值是該假設下
   得到至少同樣極端統計量的尾端機率，不是「假設為真／結果純屬運氣／有優勢」的機率，
   更不能證明未來優勢或因果能力。兩個檢定使用同一批資料，也不是兩次獨立重現。
3. **樣本外驗證**：依真實平倉時間做單一切點，前後段各至少 10 筆且不拆同時點；
   兩段都必須有顯著正期望、後段衰減小於 50%，規則也必須在看後段前凍結。
   這只是既有歷史的時間穩定度檢查，不等於真正未碰過的前瞻樣本；缺時間、混時區、
   幣別不可比、被拒資料列或未釐清的精確重複交易都會直接拒算。
4. **資料完整性閘門**：被拒列與完整時間戳交易的精確重複列會保留計數與原因，
   不靜默刪除；問題未釐清前只給 `insufficient`，不認證正優勢。
5. **賭博特徵掃描**：負期望、單筆暴賺撐場、賺小賠大、極端回撤、長連虧、純當沖…

## 建立你自己的自動化交易程式

除了單檔策略骨架，本工具還能用**互動式腳架**，為你產生一整套可執行的個人
自動化交易程式專案。生成專案會先以 `PaperBroker` 執行策略與模擬撮合；要進行
真實自動交易，使用者還必須自行接妥券商 API、只處理最新已完成 K 線的即時行情
runner，並在自訂 live runner 中沿用下方四層安全檢查。

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

**可選券商**（所有生成專案預設 `PaperBroker`；真實券商／交易所的連接完整度與
前置申請依各範本說明，API key 必須由你自行設定；執行 `brokers` 看完整清單）：

| key | 券商 | 市場 |
|-----|------|------|
| `paper` | 紙上模擬（預設，完整可用、不碰真錢） | 全 |
| `shioaji` | 永豐 Shioaji | 台股 |
| `yuanta` | 元大 SPARK API | 台股 / 期貨 |
| `fubon` | 富邦新一代 API | 台股 / 複委託 |
| `kgi` | 凱基 KGI SUPER PY | 台股 + 美股 |
| `tw_futures` | 群益 / 統一 / 元富 期貨類 | 台期 |
| `ibkr` | Interactive Brokers | 美股 / 全球 |
| `alpaca` | Alpaca | 美股 |
| `tradier` | Tradier（REST API） | 美股 |
| `binance` | Binance | 加密貨幣 |
| `pionex` | [Pionex 派網（官方現貨 REST API）](https://www.pionex.com/docs/api-docs/zh-hant) | 加密貨幣現貨 |
| `okx` | OKX | 加密貨幣 |
| `bybit` | Bybit | 加密貨幣 |
| `ccxt` | ccxt（一個介面接 100+ 交易所） | 加密貨幣 |

> 台灣券商 API 多需臨櫃簽署風險預告書、申請審核（常需數個工作天），
> 部分需安裝憑證或元件。各範本的說明已標注關鍵前置條件，實際以券商官方文件為準。

`pionex` 範本固定使用官方正式站 `https://api.pionex.com`，目前官方現貨 OpenAPI
未列 sandbox／testnet，所以不能拿正式 API 冒充模擬環境。請先留在 `PaperBroker`，
API key 只開讀取並設定 IP 白名單。Pionex 市價買單使用報價幣 `amount`，與通用
`Order.quantity` 的標的數量語意不同；範本會保守拒絕市價買單。策略用的
`client_tag` 與券商 `client_order_id` 已分離；後者可明確指定，省略時會安全產生，
避免中文策略理由或逾時重送造成識別錯誤。既有持倉若沒有可靠成本，生成策略只會
`hold`；撤單後也會重查訂單與成交，不能把「接受撤單」誤讀成零成交。代號請明寫成
`BTC_USDT`。

**可選開源圖表庫**：`lightweight`（TradingView，Apache-2.0）、`plotly`（MIT）、
`mplfinance`（BSD）、`echarts`（Apache-2.0）。

### 四層安全設計（保護你的錢）

1. **預設紙上模擬**：產出專案預設用 `PaperBroker`，完整模擬撮合但不碰真錢。
2. **完整階段連動**：不是只看樣本內裁決；只有樣本外延續、幣別與風險基準都可靠，
   且分流結果為 `tiny_live_validation` 時，設定檔才可能同時寫入
   `stage_code: tiny_live_validation` 與 `allow_live_trading: true`。
3. **Runtime 多重硬閘**：生成的 `main.py` 會重新驗證上述兩欄、
   `risk.i_have_read_disclaimer is true` 與本機 `ALLOW_LIVE_TRADING=True`，全部通過後
   才呼叫券商的雙重確認。缺欄位、錯誤型別、其他 stage 或 YAML 損壞一律 fail closed。
4. **歷史 replay 不碰真錢**：`main.py` 內建的 120 根歷史／示範 K 線只准紙上執行；
   偵測到 live broker 會硬性退出，避免把過去訊號一次送成多張真單。真實驗證必須另寫
   只處理最新已完成 K 線的 runner，並接上可驗證的即時資料源。

`PaperBroker` 是立即成交模擬器，沒有掛單佇列：買價低於現價或賣價高於現價的
非市場合理限價單會直接拒絕；可立即成交的限價單以當前報價成交，且不越過買進上限／
賣出下限。CLI 也會在寫檔前拒絕輸入／輸出別名、重複輸出路徑與既有報告檔；
`scaffold` 不覆蓋既有非空專案，沒有 `--force`。

> 預設生成的歷史／示範 runner **不會送出真單**；只有使用者自行完成 live runner、
> 填入金鑰並明確解除安全閘門後，live adapter 才可能送單。
> 真實金融交易必須由你自己操作並負全部責任。把未經驗證的賭博自動化，只會賠得更快。

## 專案結構

```
core/
  models.py            # 統一資料模型（Trade / TradeLog，含契約乘數）
  markets.py           # 市場規格：契約乘數白名單、代號辨識、槓桿標註
  onboarding.py        # 新手記錄與停手／紙上／極小額驗證分流
  analyzer.py          # 高階一行式進入點
  cli.py               # 命令列介面（18 個指令）
  report.py            # 中文文字報告
  report_html.py       # HTML 報告 + 分享圖卡（XSS 安全、自包含）
  ingest/              # CSV/JSON/Excel、自動欄位辨識、新手輸入與截圖 OCR 覆核
  metrics/             # 績效指標（performance / breakeven 轉正數字）
  verdict/             # 統計裁決引擎 + 顯著性檢定（真正的 t 分布，純標準庫）
  strategy/            # 交易模式反推 + 策略骨架 + per_tag 描述統計/反事實/跟單抽算
  backtest/            # 樣本外驗證（holdout_validate）
  trend/               # 時間趨勢：月報彙總 + 優勢衰減偵測（單一固定切點，防 p-hacking）
  montecarlo/          # 風險情境模擬：爆倉比例、最壞回撤、連虧機率
  survivorship.py      # 倖存者偏差模擬器（精確 DP，非模擬近似）
  forensics/           # 假績效統計鑑識（runs test / Lo 校正夏普 / 尾數卡方）
  antiscam/            # 反詐核心：特徵庫 + scam-check + 話術偵測 + 假老師驗證器
  broker/              # 券商抽象層 + PaperBroker + 13 種真實券商範本（共 14 種選項）
  charts/              # 四種開源圖表庫範本 + 樣式預覽
  scaffold/            # 個人交易程式專案產生器（產出自包含 broker_lib）
.claude/skills/anti-gambling-trader/SKILL.md   # Claude Code 技能包裝
.agents/skills/anti-gambling-trader/SKILL.md   # Codex／通用 agent 技能包裝
core/examples/         # 三市場範例資料（隨套件打包，pip 安裝後 demo 仍可用）
tests/                 # 自動化回歸測試（數量以 pytest 即時收集結果為準）
```

> **我們刻意不做的事**：不用班佛定律（報酬有負數、不跨數量級，前提不成立）、
> 不給「87% 是詐騙」這種未校準的假百分比、不做 Kelly 部位建議、不畫「信心度」儀表、
> 不做未來報酬投射。詳見 [方法論](docs/methodology.md)。

## 測試

```bash
# 用 pytest 一次跑全部（推薦）
python -m pip install -e ".[dev]"
python -m pytest tests/ -v

# 不裝 pytest 時，每個檔有內建執行器，需逐一執行：
python tests/test_core.py
python tests/test_trading_tools.py
python tests/test_antiscam.py
python tests/test_usability.py
python tests/test_engine.py
python tests/test_debate_fixes.py
python tests/test_trend.py
python tests/test_expansion.py
python tests/test_skill_round.py
python tests/test_round7.py
python tests/test_round8.py
python tests/test_round9.py
python tests/test_round10.py
python tests/test_line_antiscam.py
python tests/test_missing_time_semantics.py
python tests/test_onboarding.py
python tests/test_screenshot_ingest.py
python tests/test_trade_logic_audit.py
```

## 作者

好棒棒反詐協會 - 免費顧問 阿軒割割

> 誠實聲明：上列署名為社群化名，「好棒棒反詐協會」**不是**立案法人或
> 官方組織。本專案的可信度不來自頭銜，來自：公開的原始碼、可重現的
> 實驗（`experiments/`，固定 seed）、[方法論全文](docs/methodology.md)與
> 持續執行的自動化回歸測試。歡迎任何人檢驗與挑戰 —— 這正是本工具要求「老師們」
> 做到的事。

## 授權

MIT License — 開源、自由使用。詳見 [LICENSE](LICENSE)。

## ⚠️ 免責聲明

本工具為**統計分析與教育用途**，輸出**不構成任何投資建議**。
投資有風險，盈虧自負。過去績效不代表未來表現。
作者不對任何人依本工具做出的決策與後果負責。

完整免責聲明請見 [DISCLAIMER.md](DISCLAIMER.md)。
