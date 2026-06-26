"""互動式專案腳架產生器。

依交易者選擇的券商與圖表庫,產出一整套「可執行」的個人交易程式專案,
包含:設定檔、策略檔、券商接入、圖表模組、主程式(預設紙上模擬)、
README 與依賴清單。

核心安全立場:
  - 主程式預設用 PaperBroker(紙上模擬),不碰真錢。
  - 真實券商以「待填框架」附上,且受 confirm_live_trading 安全閘門保護。
  - 若有交易紀錄,會嵌入裁決結論;裁決勸退時,主程式預設禁用真實下單。
"""

from .generator import ScaffoldOptions, generate_project

__all__ = ["ScaffoldOptions", "generate_project"]
