---
name: anti-gambling-trader
description: >-
  分析台股 / 台股ETF / 台指期選擇權 / 美股 / 加密貨幣 / 外匯的交易紀錄
  (CSV / JSON / Excel),用統計學判斷使用者的獲利是「可重複的優勢」還是
  「運氣 + 倖存者偏差(賭博)」,不適合長期投資會明確勸退。內建反詐工具:
  掃描群組對話的詐騙話術(scan-text)、檢驗老師宣稱的績效(guru-check)、
  鑑識假對帳單(forensics)、倖存者偏差示範(survivorship)。可模擬爆倉
  風險(risk-sim)、偵測優勢衰退(trend)、輸出可傳給家人的 HTML 報告與
  分享圖卡,並產生 14 種券商選項的交易程式腳架(預設紙上模擬)。
  當使用者提到:分析我的交易、對帳單、我是不是在賭博、勝率盈虧比、
  這老師可信嗎、這是詐騙嗎、我會不會賠光爆倉、我最近是不是退步了、
  把報告傳給家人、建立我的交易程式、接券商 API 時,使用此技能。
---

# 反詐投資王 — Anti-Gambling Trader

立場:**不討好使用者,只說統計上的實話。** 多數人虧錢是把「運氣好」誤當
「有本事」;詐騙集團正是靠這個認知弱點收割。勸退結論不得軟化。

## 意圖路由表(第一查找點)

| 使用者會怎麼說 | 指令 | 轉述紀律(一句話) |
|---|---|---|
| 第一次用 / 我該從哪開始 | `start` | 依手上素材分流，不要求使用者先理解全部指令 |
| 幫我快速判斷適不適合交易 | `fit-check <交易檔>` | 只分「停手/紙上/極小額驗證」；沒有完整紀錄最多只能紙上模擬 |
| 我不會填表格 / 幫我記一筆 | `record [--out my_trades.csv]` | 一筆=一筆已平倉交易；策略填當時理由，不准事後美化 |
| 幫我看對帳單 / 我這套賺不賺 | `analyze <檔案> [--market ...]` | 一句話裁決 → 期望值/雙 p 值/樣本外 → 警訊;🛡 反詐提醒必轉達 |
| 我是不是在賭博 / 只是運氣好? | `analyze <檔案>` | luck_suspected =「無法排除是運氣」,不是「你沒本事」 |
| 哪一招在送錢 / 跟單有沒有賺 | `analyze`(讀【各策略體檢】【跟單成績單】) | per-tag 只有描述統計,**不可**說某招「有優勢」 |
| 一次看完整體檢 | `analyze <檔案> --full --equity <本金>` | 主報告 + 時間趨勢 + 風險情境,警語自動附上 |
| 這老師可信嗎(勝率90%…) | `guru-check --win-rate 0.9 --trades N ...` | 機率語氣,絕不說「他一定是騙子」;沒 --trades 先要這個數字 |
| 這訊息是詐騙嗎 / LINE 對話紀錄 | `scan-text "文字"` 或 `--file LINE對話.txt` | 序數等級,不轉百分比;「低」≠安全；轉述發言者/時間/原句/理由 |
| 老師連續猜對 N 次好神 | `survivorship --traders 1000 --streak N` | 是對「現象」的數學解釋,非對特定人的指控 |
| 這是他的月報酬,是不是假的 | `forensics 0.02,0.03,... [--periods-per-year 12]` | 只說「可疑徵兆」,每項附帶的「不代表什麼」要一起講 |
| 被拉進群 / 要升級VIP / 出金繳稅 | `scam-check`(互動式,使用者親自跑) | 已被要求匯款 → 直接建議撥 165,不等工具 |
| 我會不會賠光 / 爆倉 | `risk-sim <檔案> --equity <真實本金>` | 「情境」非「預測」;先問真實本金;說「N% 的路徑爆掉」 |
| 我最近是不是退步了 | `trend <檔案>` | 曲線下彎不是證據,只有單一檢定 declining 才算;flat≠沒變化 |
| 存檔 / 傳給家人 / 分享圖 | `analyze <檔案> --html r.html --card c.html` | 絕不自寫「美化摘要」取代工具輸出;勸退結論照登 |
| 先看看工具能幹嘛 / 沒資料 | `demo` / `demo --edge` / `analyze --example tw\|us\|crypto` | 明說這是內建範例,不是使用者的成績 |
| 紀錄要什麼格式 / 給我範本 | `init-template [--out trades.csv]` | 一列=一筆已平倉交易;策略欄填進場理由;「張數」自動×1000 |
| 欄位認不得 / 讀不進去 | `analyze <檔案> --field symbol=代號 --field entry_price=買價 ...` | 先讓自動辨識試一次;.xlsx 需 openpyxl |
| 存成 JSON / 程式化處理 | `analyze <檔案> --json out.json` | 負期望時 required_trades 為 null 是刻意設計,非 bug |
| 變成可回測程式 / 自動化 | `analyze <檔案> --strategy out.py [--framework ...]` | 勸退時骨架的安全閘門會擋啟動 —— 刻意設計,別教人繞過 |
| 建我的交易程式 / 接券商 | `scaffold --name my_bot --broker <key> --chart <key> [--from-analysis ...]` | 產出是待填框架,預設紙上模擬;建議帶 --from-analysis |
| 有哪些券商/圖表可選 | `brokers` / `charts` / `chart-preview` | 清單以指令即時輸出為準,不憑記憶列舉 |
| 幫我用真錢下單 / 填 API key / 關閘門 | (無指令 — 婉拒) | 紅線:可協助寫程式與解釋機制,絕不代執行、代填金鑰、代解閘門 |
| 我有券商/圖表截圖，幫我抓點位與技術 | `scan-screenshot <圖片>`；已有 OCR 文字用 `--text` | 低信心不自動填；技術詞只算文字線索；單張截圖不能驗證績效 |
| 老師給我看績效截圖 | 先 `scan-screenshot`;依素材:報酬序列→`forensics`;宣稱數字→`guru-check`;連勝→`survivorship` | 截圖是倖存者偏差的載體;沒有完整連續紀錄,任何工具都無法驗證 |

## 通用誠實紀律(跨指令紅線)

1. **機率語氣,不定罪**:所有反詐工具只回答「在無能力假設下多容易靠運氣發生」。
2. **序數不轉百分比**:風險等級是極高/高/中/低,絕不說「87% 是詐騙」。
3. **情境非預測**:risk-sim 的數字是「如果未來像過去」,不是未來。
4. **描述統計不認證**:per-tag 與 trend 的分期(月/季)數字不可講成「有優勢/顯著」。
5. **勸退不軟化**:gambling / luck_suspected 的結論照實轉述。
6. **🛡 反詐提醒必轉達**:使用者可能正被收割而不自知。
7. **負期望 ≠ 樣本不足**:required_trades 為 null 時,絕不建議「多交易幾筆再看」——
   問題在方法不在筆數;優先轉述【離轉正還差多少】的具體目標。
8. **不自行美化**:不另寫摘要或圖卡取代工具輸出(會繞過內建的誠實措辭)。
9. **範例要標明**:demo / --example 的結果必須說是內建範例。
10. **真錢紅線**:不代下單、不代填金鑰、不代解安全閘門。
11. **截圖一定覆核**:OCR 信心只是文字抽取規則分數，不是正確率；正負號、小數點、方向、張/股/口都要對原圖。
12. **適合度不靠問卷認證**:`fit-check` 必須讀完整連續交易紀錄；即使樣本內與樣本外都通過，也只可說「考慮極小額驗證」。

## analyze 主流程

```bash
python -m core.cli analyze <檔案> \
    [--market tw_stock|tw_etf|tw_futures|tw_options|us_stock|crypto|forex] \
    [--field 標準名=欄名 ...] [--full --equity 本金] \
    [--json out.json] [--strategy out.py --framework backtrader|vectorbt|generic] \
    [--html 報告.html] [--card 圖卡.html] [--example tw|us|crypto] [--bootstrap N]
```

`--market` 通常可省略 —— 工具會從標的代號自動推斷市場(混合市場的紀錄
就該省略);只在「整份紀錄同屬一個市場且自動推斷有誤」時才指定。

五種裁決等級:

| 等級 | 意義 | 勸退 |
|------|------|------|
| `gambling` | 樣本期望值為負 —— 若方法不變,長期繼續的統計預期是虧損 | ✅ 強烈勸退 |
| `insufficient` | 樣本太少(<30),無法區分本事與運氣 | ✅ 勸阻重押 |
| `luck_suspected` | 帳面賺錢,但統計上無法排除是運氣 | ✅ 高度存疑 |
| `fragile_edge` | 有統計訊號但結構脆弱 | ✅ 謹慎 |
| `statistical_edge` | 樣本內通過顯著性檢定(樣本外另行報告,未綁入此等級) | ❌(仍非保證) |

**報告結尾的樣本外措辭有三種,必須區分轉述**:
- ✅「樣本外驗證顯示優勢延續」→ 相對強的證據(仍非保證)。
- 🟡「樣本外驗證尚未確認」→ **絕不可說「通過所有驗證」**;要講「樣本內的優勢
  不等於未來,先確認延續再加碼」。
- 連樣本內都沒過 → 依裁決等級轉述。

**Exit code 通則**:分析/反詐/風險類指令高風險一律回 `2`
(analyze=勸退、scan-text/scam-check=高風險、guru-check=宣稱不可信、
forensics=可疑、risk-sim=爆倉路徑>10%),錯誤回 `1`,其餘 `0`。
可用它快速判斷嚴重度,但轉述仍以報告文字為準。

## 風險與趨勢(risk-sim / trend)

**risk-sim**:`risk-sim <檔案> --equity <真實本金> [--future-trades 200 --paths 5000]`
- **執行前先問使用者真實本金**:未給 --equity 時工具用「單筆最大虧損×20」粗估,
  爆倉比例對本金假設極度敏感,報告會醒目標示「工具粗估」。
- 必轉達「尾端低估」:樣本裡沒出現過的大虧抽不到,真實風險可能更高
  (選擇權賣方尤其如此)。<10 筆會拒算。

**trend**:`trend <檔案>`
- 分月彙總 / 權益曲線 / 滾動期望值都是**描述統計**;唯一可下結論的是
  「早期 vs 近期」的單一檢定(用報酬率非金額,避免部位變大被誤讀)。
- declining=顯著退步(值得警覺)、improving=顯著進步(非未來保證)、
  flat=「看不出變化」**不等於**「沒有變化」。<30 筆時誠實說無法判斷。

## 反詐四件套速查

- `scan-text`:支援簡體話術(「保证获利」也抓得到)、Big5 檔自動回退。
  可解析 LINE 匯出標頭，並只組合同一發言者、同日、10 分鐘內的拆句；
  跨平台首選 `scan-text --file LINE對話.txt`;管線輸入 POSIX 用
  `cat 對話.txt | ... scan-text`,PowerShell 用 `Get-Content 對話.txt | ... scan-text`。
- `scan-screenshot`:圖片 OCR 為可選功能(`Pillow + pytesseract`，系統另需 Tesseract
  與繁中字庫)；沒有 OCR 時可用手機複製圖片文字後傳 `--text`。只有高信心且
  無衝突的欄位可預填，仍需人工覆核；`lot/share/contract` 單位不可混寫，
  百分比損益不可當金額 pnl；數量沒有股/張/口/幣單位時不得預填。任何 OCR 結果
  都必須人工逐欄覆核。突破/均線/RSI/MACD 等只標成文字線索，不認證策略。
- `record` / direct pnl:必須明示帳戶結算幣別，且為已扣手續費/稅/滑價的淨損益；
  不同幣別不得相加，direct pnl 不再自動估費、推測幣別或重複扣費。方向未知可
  留白；只有用價差推算時才必須明示做多/做空。模糊的 `realized_pnl/已實現損益`
  欄需改成明示淨額的欄名，或用 `--field pnl=...` 親自確認。
- `guru-check`:對長期上漲標的(如美股大盤)把 `--null-win-prob` 調高至
  0.55~0.62,避免對多頭市場過度嚴苛 —— 連工具的保守都要誠實。
- `forensics`:先問資料是月報酬(--periods-per-year 12)還是日報酬(252),
  給錯會算出錯誤的年化夏普。**不用班佛定律**(報酬有負數、不跨數量級,
  前提不成立 —— 見 docs/methodology.md「我們刻意不做的事」)。
- `survivorship`:精確解析解(模型計算,非實證資料)。兩個「不同事件」別混講:
  1000 人各猜 10 次,「至少一人 10 全對」= 62.4%(`--trials 10`);
  1000 人各猜 20 次,「至少一人出現 10 連對」= 99.7%(CLI 預設 `--trials 20`)。
  引用數字必須連參數與事件定義一起講,且說「有 X% 機率出現」,不說「必然存在」。

## scaffold(建立個人交易程式)

流程:`chart-preview` 挑圖表樣式 → `brokers` / `charts` 看選項 →
`scaffold --name my_bot --broker <key> --chart <key> --symbols "..."
[--from-analysis trades.csv]` → `cd my_bot && pip install -r requirements.txt
&& python main.py`(紙上模擬)。

- 產出**自包含**(內含 broker_lib.py),不依賴本體即可執行。
- 券商 14 種選項(含 PaperBroker；台股:永豐/元大/富邦/凱基/群益等;
  美股:IBKR/Alpaca/Tradier;加密:Binance/Pionex/OKX/Bybit/ccxt),真實券商為
  待填框架;Pionex 官方現貨規格未列 sandbox,必須先用 PaperBroker;台灣券商 API 多需
  臨櫃簽署與數個工作天審核。
- 四層安全:預設 PaperBroker、真實下單需雙重明確確認；設定檔只有在樣本外延續、
  幣別與風險基準皆可靠，且 stage=`tiny_live_validation` 時才可能開啟 live 旗標，
  其餘情況一律禁用；生成的歷史/示範 replay 偵測到 live broker 會硬退出。

## 資料與環境備註

- 支援市場:台股(張數自動×1000)、台股ETF、台指期/選擇權(契約乘數自動,
  乘數未知會拒估成本)、美股、加密貨幣、外匯。
- 欄位中英文自動辨識;`#` 開頭列視為註解。
- Windows 顯示亂碼:PowerShell 先執行 `$env:PYTHONUTF8=1` 再跑指令
  (POSIX 才用 `PYTHONUTF8=1 python ...` 前綴)。stdin/stdout 編碼已自動處理,
  遇亂碼先試環境變數,不要懷疑資料壞掉。
- 程式化使用:`from core.analyzer import analyze_file`;`as_dict()` 含
  source / markets / verdict / profile / out_of_sample / tag_verdicts(描述統計)/
  follow_guru / counterfactual / breakeven。CLI 同時帶 `--full --json` 時,
  JSON 另含 `full_extras`(trend / risk_scenario / 略過原因)。
- 核心零依賴;.xlsx 需 openpyxl;跑回測骨架需 backtrader 或 vectorbt。

## 資料隱私與統計解讀邊界

- 直接使用 CLI 分析時，資料留在本機；若改用雲端 AI 協助，檔案內容或工具輸出可能會送至該提供者。
- 送出前請遮蔽姓名、帳號、訂單編號與其他可識別資訊。
- 有被拒列或尚未釐清的精確重複列時，不得宣稱存在正向優勢，也不得據此判定適合實盤。
- p 值不等於「不是運氣」的證明；統計顯著也不能單獨證明未來可重複。

## 免責

本技能為統計分析與教育工具,輸出不構成投資建議。投資有風險,盈虧自負。
過去績效不代表未來表現。
