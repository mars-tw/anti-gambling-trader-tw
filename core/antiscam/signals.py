"""把交易分析結果,連結到對應的詐騙警語。

當一份交易紀錄呈現出某些特徵時,往往正好是某種詐騙的受害軌跡。
例如:大量虧損 + 標的高度集中 + tag 寫著「老師推薦」→ 很可能是跟單假飆股群。
這個模組把「分析結果」翻譯成「你可能正在被哪種詐騙收割」的具體提醒。
"""

from __future__ import annotations

from ..metrics.performance import PerformanceMetrics
from ..strategy.profiler import StrategyProfile
from .patterns import find_pattern

# 暗示「跟單 / 聽明牌」的 tag 關鍵字
_FOLLOW_KEYWORDS = (
    "老師", "明牌", "報明牌", "帶單", "群", "vip", "內線", "消息", "推薦",
    "跟單", "名師", "分析師", "飆股",
)


def scam_warnings_for(
    metrics: PerformanceMetrics,
    profile: StrategyProfile,
) -> list[str]:
    """依分析結果,回傳與詐騙相關的具體警語(可能為空)。"""
    warnings: list[str] = []

    # 1. tag 出現「跟單 / 聽明牌」字眼 + 整體虧損 → 假飆股群受害軌跡
    follow_tags = [
        tag for tag in (profile.tags or {})
        if any(k in str(tag).lower() for k in _FOLLOW_KEYWORDS)
    ]
    if follow_tags and metrics.expectancy < 0:
        p = find_pattern("fake_stock_group")
        warnings.append(
            f"🚨 你的虧損交易中,有標籤像是『跟單 / 聽明牌』({', '.join(follow_tags[:3])}),"
            f"而整體期望值為負。這是『假飆股群 / 假老師』受害者的典型軌跡 —— "
            f"{p.rebuttal if p else ''}"
        )

    # 2. 高勝率 + 負期望 → 正中「高勝率話術」的陷阱
    if metrics.win_rate > 0.6 and metrics.expectancy < 0:
        p = find_pattern("guaranteed_return")
        warnings.append(
            f"⚠️ 你的勝率有 {metrics.win_rate:.0%},帳面上『常常贏』,但期望值卻是負的"
            f"(賺小賠大)。這正是『高勝率 = 賺錢』話術最會騙人的地方 —— "
            f"{p.rebuttal if p else ''}"
        )

    # 3. 獲利高度集中單筆 → 像被「精選截圖」誤導去追的單一行情
    if metrics.top_trade_pnl_share > 0.6 and metrics.wins > 1:
        warnings.append(
            f"⚠️ 你的總獲利有 {metrics.top_trade_pnl_share:.0%} 來自單獨一筆。"
            "若你是看了某『績效截圖 / 名師見證』才去追那一波,請記得:"
            "你看到的是倖存者偏差 —— 沒人給你看賠光退場的那些人。"
        )

    # 4. 純當沖 / 極短線 + 虧損 → 常見於被帶進『當沖致富』的群組
    if profile.style_code == "scalp_intraday" and metrics.expectancy < 0:
        warnings.append(
            "🟡 你以當沖 / 極短線為主且整體虧損。若你是被『當沖月入百萬』之類的"
            "群組或課程吸引進場,請冷靜:能靠當沖長期穩定獲利的是極少數,"
            "多數人是被高頻成本與雜訊慢慢磨光本金。"
        )

    return warnings
