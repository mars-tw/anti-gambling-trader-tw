"""反詐騙模組 — 這個工具存在的真正原因。

台灣的投資詐騙氾濫:假飆股群、假 VIP 群、假二群、假名師、假績效截圖、
保證獲利話術、誊騙幣與假投資平台。它們的共同手法,就是用「倖存者偏差」
與「精心挑選的截圖」讓你誤以為有穩賺的捷徑。

這個模組提供:
  - SCAM_PATTERNS:常見投資詐騙的特徵知識庫
  - run_scam_check():互動式詐騙風險自我檢測清單
  - scam_warnings_for():依交易分析結果,點出與詐騙相關的警訊

核心信念:真正的優勢經得起統計檢定與時間考驗;而詐騙最怕的,
就是你冷靜地用數學檢驗它的承諾。這個工具就是要把那份冷靜還給你。
"""

from .patterns import SCAM_PATTERNS, ScamPattern
from .checklist import ScamCheckResult, run_scam_check
from .signals import scam_warnings_for

__all__ = [
    "SCAM_PATTERNS",
    "ScamPattern",
    "ScamCheckResult",
    "run_scam_check",
    "scam_warnings_for",
]
