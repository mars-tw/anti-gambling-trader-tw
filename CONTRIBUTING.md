# 貢獻指南

感謝你想讓這個反詐工具更好。開始之前,請先理解本專案的四條不可協商的
核心價值 —— PR 是否被接受,以此為最高標準:

1. **誠實**:不給假精準。未經校準的百分比、信心度儀表、未來報酬投射,
   一律不收(詳見 [方法論:我們刻意不做的事](docs/methodology.md))。
2. **保守**:寧可錯殺。統計上無法排除運氣,就勸退。
3. **可解釋**:輸出必須讓沒學過統計的散戶看得懂。
4. **零依賴**:`core/` 只用 Python 標準庫(`.xlsx` 的 openpyxl 為選配)。

## 開發流程

```bash
git clone https://github.com/mars-tw/anti-gambling-trader-tw.git
cd anti-gambling-trader-tw
python -m pip install -e ".[dev]"
python -m pytest tests/ -q     # 全部測試必須通過；數量以當次 pytest 收集為準
```

- 修 bug 請附回歸測試;統計相關的修改請附可重現實驗(參考 `experiments/`
  的寫法:固定 seed、純標準庫)。
- 文案修改請對照誠實紀律:機率語氣、不定罪、序數不轉百分比。
- 新增券商範本:在 `core/broker/registry_*.py` 加 `BrokerTemplate`,
  並同步 `core/scaffold/templates.py` 的 `_CREDENTIAL_FIELDS`
  (有測試會抓不同步)。

## 回報問題

- 一般 bug / 建議:開 GitHub Issue,附重現步驟。
- 誤判回報(把正常標的/文字判成高風險,或反之):請附輸入內容與
  預期行為 —— 反詐工具的誤殺與漏抓我們都當 bug 處理。
