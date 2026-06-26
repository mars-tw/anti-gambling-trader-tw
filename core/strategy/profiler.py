"""交易模式剖析:從歷史交易反推「使用者實際上在做什麼」。

我們無法讀心,但可以從交易的客觀特徵(持倉時間、方向偏好、
標的集中度、進出場時間分布、tag 標籤……)推斷出交易風格,
作為產生策略骨架的依據。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..models import Market, Side, TradeLog


@dataclass
class StrategyProfile:
    """一份交易紀錄反推出的策略輪廓。"""

    style: str                              # 推斷的交易風格(中文描述)
    style_code: str                         # 機器可讀風格碼
    dominant_market: Market                 # 主要市場
    dominant_side: Side                     # 主要方向
    avg_holding_days: float
    median_holding_days: float
    symbol_concentration: float             # 前三大標的佔交易比例
    distinct_symbols: int
    tags: dict[str, int] = field(default_factory=dict)   # tag → 次數
    per_tag_winrate: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["dominant_market"] = self.dominant_market.value
        d["dominant_side"] = self.dominant_side.value
        return d


def _classify_style(avg_days: float, intraday_ratio: float) -> tuple[str, str]:
    """依平均持倉天數與當沖比例分類交易風格。"""
    if intraday_ratio > 0.7:
        return ("當沖 / 極短線(高頻、高成本、最接近賭博的型態)", "scalp_intraday")
    if avg_days < 5:
        return ("短線波段(數日內進出)", "swing_short")
    if avg_days < 30:
        return ("中期波段(數週)", "swing_medium")
    if avg_days < 180:
        return ("中長期持有(數月)", "position")
    return ("長期投資(半年以上)", "long_term")


def profile_strategy(log: TradeLog) -> StrategyProfile:
    """剖析交易紀錄,產出策略輪廓。"""
    trades = list(log)
    n = len(trades)

    if n == 0:
        return StrategyProfile(
            style="無資料", style_code="empty",
            dominant_market=Market.UNKNOWN, dominant_side=Side.LONG,
            avg_holding_days=0, median_holding_days=0,
            symbol_concentration=0, distinct_symbols=0,
        )

    holding = sorted(t.holding_days for t in trades)
    avg_days = sum(holding) / n
    median_days = holding[n // 2]
    # 當沖判定統一用 Trade.is_day_trade(同一交易日),與成本估算口徑一致
    intraday_ratio = sum(1 for t in trades if t.is_day_trade) / n

    market_counts = Counter(t.market for t in trades)
    side_counts = Counter(t.side for t in trades)
    symbol_counts = Counter(t.symbol for t in trades)

    dominant_market = market_counts.most_common(1)[0][0]
    dominant_side = side_counts.most_common(1)[0][0]
    distinct_symbols = len(symbol_counts)
    top3 = sum(c for _, c in symbol_counts.most_common(3))
    concentration = top3 / n

    style, style_code = _classify_style(avg_days, intraday_ratio)

    # tag 統計 + 各 tag 的勝率(這是反推「哪套邏輯有效」的關鍵)
    tag_counts: Counter[str] = Counter()
    tag_wins: Counter[str] = Counter()
    for t in trades:
        if t.tag:
            tag_counts[t.tag] += 1
            if t.is_win:
                tag_wins[t.tag] += 1
    per_tag_winrate = {
        tag: tag_wins[tag] / cnt for tag, cnt in tag_counts.items() if cnt > 0
    }

    notes: list[str] = []
    if concentration > 0.7 and distinct_symbols < 5:
        notes.append(
            f"交易高度集中在少數標的(前三大佔 {concentration:.0%})。"
            "績效可能綁定特定標的的單一行情,換標的未必複製得了。"
        )
    if not tag_counts:
        notes.append(
            "資料中沒有策略標籤(tag)。建議未來每筆交易標註進場理由,"
            "才能分辨是哪一套邏輯真正帶來獲利。"
        )
    if intraday_ratio > 0.7:
        notes.append(
            "以當沖為主:這類交易長期能穩定獲利的比例極低,且成本侵蝕嚴重。"
        )

    return StrategyProfile(
        style=style,
        style_code=style_code,
        dominant_market=dominant_market,
        dominant_side=dominant_side,
        avg_holding_days=avg_days,
        median_holding_days=median_days,
        symbol_concentration=concentration,
        distinct_symbols=distinct_symbols,
        tags=dict(tag_counts),
        per_tag_winrate=per_tag_winrate,
        notes=notes,
    )
