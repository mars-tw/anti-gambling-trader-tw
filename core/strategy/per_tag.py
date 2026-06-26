"""逐策略(per-tag)完整裁決、反事實分析、跟單抽算。

這是把工具從「整體體檢」升級成「精準定位」的關鍵:
  - per_tag_verdicts:對每個策略標籤各下一次完整裁決(期望值/顯著性/等級),
    讓使用者知道『到底是哪一招在送錢、哪一招其實有救』。
  - counterfactual:如果停掉表現最差的那一招,整體會變怎樣。
  - follow_the_guru:把『聽老師/明牌/跟單』的交易單獨抽出來算期望值 ——
    兌現反詐金句裡『把老師推薦記下來算期望值,幾乎都是負的』的承諾。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..antiscam.signals import _FOLLOW_KEYWORDS
from ..metrics.performance import PerformanceMetrics, compute_metrics
from ..models import TradeLog
from ..verdict.judge import Verdict, VerdictLevel, judge


@dataclass
class TagVerdict:
    """單一策略標籤的裁決摘要。"""

    tag: str
    n_trades: int
    expectancy: float
    win_rate: float
    profit_factor: float
    total_pnl: float
    level: VerdictLevel
    is_significant: bool
    low_sample: bool          # 樣本太少,結論僅供參考


@dataclass
class CounterfactualResult:
    """反事實分析:停掉最差策略後的對照。"""

    worst_tag: str
    before_expectancy: float
    after_expectancy: float
    before_level: VerdictLevel
    after_level: VerdictLevel
    before_total_pnl: float
    after_total_pnl: float
    message: str = ""


@dataclass
class FollowGuruResult:
    """跟單 / 聽明牌交易的專屬裁決。"""

    n_trades: int
    expectancy: float
    total_pnl: float
    win_rate: float
    level: VerdictLevel
    follow_tags: list[str] = field(default_factory=list)
    message: str = ""


def per_tag_verdicts(
    log: TradeLog,
    *,
    min_tag_trades: int = 5,
    n_bootstrap: int = 1500,
) -> list[TagVerdict]:
    """對每個有足夠樣本的策略標籤各下一次裁決,依期望值由低到高排序。

    最差的排最前面 —— 因為使用者最需要先看到『該砍哪一招』。
    """
    tags = {t.tag for t in log if t.tag}
    results: list[TagVerdict] = []
    for tag in tags:
        sub = log.filter_by_tag(tag)
        if len(sub) < 2:
            continue
        m = compute_metrics(sub)
        # 小 tag 用較低的 min_trades 門檻,但誠實標注樣本少
        low_sample = len(sub) < 30
        v = judge(sub, metrics=m, n_bootstrap=n_bootstrap,
                  min_trades=min(min_tag_trades, 30))
        results.append(TagVerdict(
            tag=tag,
            n_trades=len(sub),
            expectancy=m.expectancy,
            win_rate=m.win_rate,
            profit_factor=m.profit_factor,
            total_pnl=m.total_pnl,
            level=v.level,
            is_significant=v.significance.is_significant,
            low_sample=low_sample,
        ))
    results.sort(key=lambda r: r.expectancy)   # 最差(最該砍)的排最前
    return results


def counterfactual_drop_worst(
    log: TradeLog,
    *,
    tag_verdicts: list[TagVerdict] | None = None,
    n_bootstrap: int = 2000,
) -> CounterfactualResult | None:
    """如果停掉『期望值最差』的策略標籤,整體會變怎樣。

    回傳 None 表示無法分析(無 tag、或只有一個 tag、或砍了就沒交易了)。
    """
    tv = tag_verdicts if tag_verdicts is not None else per_tag_verdicts(log)
    if len(tv) < 2:
        return None   # 至少要有兩個 tag 才談「砍掉一個」

    worst = tv[0]
    if worst.expectancy >= 0:
        return None   # 最差的都沒在送錢,沒必要建議砍

    before_m = compute_metrics(log)
    before_v = judge(log, metrics=before_m, n_bootstrap=n_bootstrap)

    after_log = log.filter(lambda t: t.tag != worst.tag, label=f"drop_{worst.tag}")
    if len(after_log) < 2:
        return None
    after_m = compute_metrics(after_log)
    after_v = judge(after_log, metrics=after_m, n_bootstrap=n_bootstrap)

    delta_exp = after_m.expectancy - before_m.expectancy
    msg = (
        f"光是停掉『{worst.tag}』這一招,整體每筆期望值就從 "
        f"{before_m.expectancy:+.2f} 變成 {after_m.expectancy:+.2f}"
        f"(改善 {delta_exp:+.2f}),裁決從「{_lvl(before_v.level)}」"
        f"變成「{_lvl(after_v.level)}」。"
    )
    if before_v.should_discourage and not after_v.should_discourage:
        msg += " ← 換句話說:不做這一招,你其實是有救的。"

    return CounterfactualResult(
        worst_tag=worst.tag,
        before_expectancy=before_m.expectancy,
        after_expectancy=after_m.expectancy,
        before_level=before_v.level,
        after_level=after_v.level,
        before_total_pnl=before_m.total_pnl,
        after_total_pnl=after_m.total_pnl,
        message=msg,
    )


def follow_the_guru(
    log: TradeLog,
    *,
    n_bootstrap: int = 2000,
) -> FollowGuruResult | None:
    """把『聽老師 / 明牌 / 跟單 / VIP 群』的交易單獨抽出來算期望值。

    這兌現了反詐金句裡的承諾:『把老師的歷史推薦全部記下來、算期望值 ——
    幾乎沒有例外,都是負的。』用使用者自己的錢的數字,坐實跟單必賠。

    回傳 None 表示沒有可辨識的跟單交易。
    """
    def is_follow(t) -> bool:
        return bool(t.tag) and any(k in str(t.tag).lower() for k in _FOLLOW_KEYWORDS)

    sub = log.filter(is_follow, label="follow_guru")
    if len(sub) < 2:
        return None

    follow_tags = sorted({t.tag for t in sub if t.tag})
    m = compute_metrics(sub)
    v = judge(sub, metrics=m, n_bootstrap=n_bootstrap, min_trades=5)

    if m.expectancy < 0:
        msg = (
            f"你『聽老師 / 跟單 / 明牌』的 {len(sub)} 筆交易,"
            f"每筆平均賠 {abs(m.expectancy):.2f},合計 {m.total_pnl:+.2f}。"
            "這就是用你自己的錢算出來的鐵證 —— 跟單不但沒讓你賺,還在穩定地讓你賠。"
            "那些『老師』真正賺的,是你的學費與群費,不是市場。"
        )
    else:
        msg = (
            f"你『聽老師 / 跟單 / 明牌』的 {len(sub)} 筆交易,期望值為 "
            f"{m.expectancy:+.2f}。即使帳面為正,也要警覺:這可能只是運氣,"
            "而且你看到的『老師神準』往往是倖存者偏差。請持續用統計檢驗,別輕信。"
        )

    return FollowGuruResult(
        n_trades=len(sub),
        expectancy=m.expectancy,
        total_pnl=m.total_pnl,
        win_rate=m.win_rate,
        level=v.level,
        follow_tags=follow_tags,
        message=msg,
    )


def _lvl(level: VerdictLevel) -> str:
    """等級中文名(集中定義於 VerdictLevel.display_name)。"""
    return level.display_name
