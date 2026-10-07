"""裁決引擎:把績效指標 + 統計檢定,翻譯成一個誠實的判斷。

判斷的精神:
    寧可錯殺(把賭博誤判為賭博),不可放過(把賭博說成優勢)。
    因為前者讓使用者多存疑、多驗證,代價是時間;
    後者讓使用者誤信自己有本事而重押,代價可能是畢生積蓄。

所以所有臨界值都偏保守。一個策略要被認證為「具統計優勢」,
門檻很高;只要有任何賭博特徵,就會被點名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..metrics.performance import PerformanceMetrics, compute_metrics, fmt_ratio
from ..models import TradeLog
from .statistics import (
    SignificanceResult,
    required_sample_size,
    required_sample_size_from_pnls,
    test_expectancy_positive,
)


class VerdictLevel(str, Enum):
    """裁決等級,由壞到好。"""

    GAMBLING = "gambling"               # 這是賭博 — 強烈勸退
    INSUFFICIENT = "insufficient"       # 樣本不足 — 還無法判斷,別急著相信自己
    LUCK_SUSPECTED = "luck_suspected"   # 帳面賺錢,但統計上像運氣 — 高度存疑
    FRAGILE_EDGE = "fragile_edge"       # 有微弱優勢但脆弱 — 謹慎,需更多驗證
    STATISTICAL_EDGE = "statistical_edge"  # 具統計顯著的優勢 — 但仍非保證

    @property
    def display_name(self) -> str:
        """中文等級名(單一事實來源,供 report / per_tag 共用)。"""
        return _LEVEL_DISPLAY[self]

    @property
    def badge(self) -> str:
        """帶紅綠燈 emoji 的徽章。"""
        return _LEVEL_BADGE[self]


# 集中定義各等級的中文名與徽章,避免 report / per_tag 各自硬編一份。
_LEVEL_DISPLAY = {
    VerdictLevel.GAMBLING: "賭博",
    VerdictLevel.INSUFFICIENT: "樣本不足",
    VerdictLevel.LUCK_SUSPECTED: "疑似運氣",
    VerdictLevel.FRAGILE_EDGE: "脆弱優勢",
    VerdictLevel.STATISTICAL_EDGE: "具統計優勢",
}
_LEVEL_BADGE = {
    VerdictLevel.GAMBLING: "🟥 賭博",
    VerdictLevel.INSUFFICIENT: "🟧 樣本不足",
    VerdictLevel.LUCK_SUSPECTED: "🟨 疑似運氣",
    VerdictLevel.FRAGILE_EDGE: "🟨 脆弱優勢",
    VerdictLevel.STATISTICAL_EDGE: "🟩 具優勢",
}


@dataclass
class RedFlag:
    """一個被偵測到的「賭博/不穩定」警訊。"""

    code: str
    severity: str       # "high" | "medium" | "low"
    message: str


@dataclass
class Verdict:
    """完整裁決結果。"""

    level: VerdictLevel
    should_discourage: bool                 # 是否該「勸退」使用者
    headline: str                           # 一句話結論
    metrics: PerformanceMetrics
    significance: SignificanceResult
    required_trades: int                    # 估計需要的樣本數
    red_flags: list[RedFlag] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)   # 支持此裁決的理由
    advice: list[str] = field(default_factory=list)     # 給使用者的具體建議

    def as_dict(self) -> dict:
        # 期望值 ≤ 0 時,「需要多少樣本」沒有意義(再多樣本也無法把負期望變優勢),
        # 不應把內部哨兵值(9999)洩漏到 JSON;與文字報告口徑一致,輸出 None。
        req = None if (self.metrics.expectancy or 0) <= 0 else self.required_trades
        return {
            "level": self.level.value,
            "should_discourage": self.should_discourage,
            "headline": self.headline,
            "required_trades": req,
            "metrics": self.metrics.as_dict(),
            "significance": self.significance.__dict__,
            "red_flags": [rf.__dict__ for rf in self.red_flags],
            "reasons": self.reasons,
            "advice": self.advice,
        }


# ── 賭博特徵掃描 ─────────────────────────────────────────────
def _scan_red_flags(m: PerformanceMetrics, sig: SignificanceResult) -> list[RedFlag]:
    flags: list[RedFlag] = []

    # 1. 負期望值:長期統計預期為虧損,卻可能短期帳面為正(典型賭博)
    if m.expectancy < 0:
        flags.append(RedFlag(
            "negative_expectancy", "high",
            f"每筆交易的樣本期望值為負({m.expectancy:.2f})。"
            "方法不變的話,長期繼續的統計預期就是虧損。"
        ))

    # 2. 獲利集中於少數暴賺:像中樂透,不是穩定優勢
    # wins >= 1:「唯一一筆獲利佔 100%」正是最極端的集中,不可反而漏掉
    if m.top_trade_pnl_share > 0.5 and m.wins >= 1:
        flags.append(RedFlag(
            "concentrated_profit", "high",
            f"光是最賺的一筆,就佔了總獲利的 {m.top_trade_pnl_share:.0%}。"
            "你的『獲利』高度依賴單次幸運,而非可重複的方法。"
        ))

    # 3. 高勝率 + 極差盈虧比:典型「賺小賠大」,一次爆倉吃光所有獲利
    if m.win_rate > 0.7 and m.payoff_ratio < 0.4 and m.losses > 0:
        flags.append(RedFlag(
            "win_small_lose_big", "high",
            f"勝率高達 {m.win_rate:.0%},但盈虧比只有 {m.payoff_ratio:.2f}。"
            "這是『常贏小錢、偶爾賠大錢』的危險結構,一次大虧就會回吐所有獲利。"
        ))

    # 3b. 安全邊際過薄:期望值雖為正,但盈虧比只比「打平門檻」高一點點。
    #     硬切點(payoff<0.4)會漏掉「payoff 剛好 0.5、勝率 0.7」這種
    #     正 EV 卻極脆弱的結構 —— 勝率只要小幅下滑就會由正翻負。
    #     用連續的 edge_margin 補上,但保持白話可解釋。
    if m.expectancy > 0 and m.wins > 0 and m.losses > 0:
        # 勝率分母包含打平交易,所以敗率必須用實際 losses/total；
        # (1-win_rate) 會把打平全當虧損,把門檻誇大。
        loss_rate = m.losses / m.total_trades
        breakeven_payoff = loss_rate / m.win_rate   # 讓期望值 = 0 的盈虧比
        edge_margin = m.payoff_ratio - breakeven_payoff
        if 0 < edge_margin < 0.25 * max(breakeven_payoff, 1e-9):
            flags.append(RedFlag(
                "thin_edge_margin", "medium",
                f"你的盈虧比 {fmt_ratio(m.payoff_ratio)} 只比打平門檻 {breakeven_payoff:.2f} "
                f"高一點點(安全邊際 {edge_margin:.2f})。"
                f"以你 {m.win_rate:.0%} 的勝率,只要勝率、平均獲利、平均虧損或成本"
                "稍微惡化,期望值就會由正翻負。這種優勢很脆弱。"
            ))

    # 4. 極端回撤:即使最終獲利,過程中也曾瀕臨毀滅。
    #    high 而非 medium —— 帳戶曾腰斬的策略不可拿「具統計優勢」的綠色
    #    裁決(決策樹只用 high 警訊降級,medium 擋不住)。寧可錯殺。
    #    只有回撤 % 可靠(有資本基準)時才發:pnl-only 資料的 % 是
    #    「無法計算」,報告都這麼說了,裁決卻拿它定罪是兩個通道打架。
    if (
        m.sequence_metrics_reliable
        and m.drawdown_pct_reliable
        and m.max_drawdown_pct > 0.5
    ):
        flags.append(RedFlag(
            "severe_drawdown", "high",
            f"最大回撤達 {m.max_drawdown_pct:.0%}。"
            "這代表過程中你的帳戶曾腰斬以上 — 多數人撐不過這種壓力。"
        ))

    # 5. 連續虧損過長:心理上極難承受,實務上常導致中途放棄或亂改規則
    if m.sequence_metrics_reliable and m.max_consecutive_losses >= 8:
        flags.append(RedFlag(
            "long_losing_streak", "medium",
            f"曾連續虧損 {m.max_consecutive_losses} 次。"
            "請誠實問自己:連賠這麼多次,你還守得住原本的紀律嗎?"
        ))

    # 6. 全為當沖/極短線 + 統計不顯著:最接近賭場的交易型態
    if m.is_mostly_intraday and not sig.is_significant:
        flags.append(RedFlag(
            "intraday_noise", "medium",
            "你的交易以當沖/極短線為主,且統計上看不出穩定優勢。"
            "高頻短線的成本與雜訊極高,長期勝出者鳳毛麟角。"
        ))

    # 7. 獲利因子過低:總獲利幾乎等於總虧損,毫無安全邊際
    if 0 < m.profit_factor < 1.1 and m.total_trades >= 20:
        flags.append(RedFlag(
            "thin_profit_factor", "low",
            f"獲利因子僅 {m.profit_factor:.2f}(總獲利 ÷ 總虧損)。"
            "幾乎是在原地打轉,扣掉沒算到的成本後很可能其實在虧。"
        ))

    return flags


def judge(
    log: TradeLog,
    *,
    metrics: PerformanceMetrics | None = None,
    min_trades: int = 30,
    n_bootstrap: int = 5000,
) -> Verdict:
    """對一份交易紀錄做出最終裁決。

    Args:
        log:         交易紀錄
        metrics:     若已算過可傳入避免重算
        min_trades:  低於此筆數一律視為「樣本不足」,不下優勢結論
        n_bootstrap: bootstrap 次數

    Returns:
        Verdict
    """
    # TradeLog.trades 是公開可變 list；先重新檢查，避免建構後 append / extend
    # 的精確重複列被舊計數略過而誤判成正優勢。
    log.refresh_integrity()
    m = metrics or compute_metrics(log)
    pnls = [t.pnl or 0.0 for t in log]
    sig = test_expectancy_positive(pnls, n_bootstrap=n_bootstrap)

    # 所需樣本量:有足夠實際損益,或至少同時看過輸贏兩種結果時,用
    # 「真實樣本變異」估算(較貼近現實)。只有單一結果類型的小樣本才
    # 退回二項模型;它只是最樂觀的下限,因為它假設組內零變異。
    #
    # 不能只把 total>=10 當開關:少於10筆且含打平交易時,二項模型會把
    # 打平全算成失敗(1-win_rate),甚至把實際正期望錯算成負 edge→9999。
    req = None
    if len(pnls) >= 10 or (m.wins > 0 and m.losses > 0):
        req = required_sample_size_from_pnls(pnls)
    if req is None:
        req = required_sample_size(m.win_rate, m.payoff_ratio)

    flags = _scan_red_flags(m, sig)

    rejected_rows = int(getattr(log, "rejected_row_count", 0) or 0)
    duplicate_rows = int(getattr(log, "suspected_duplicate_count", 0) or 0)
    if rejected_rows:
        flags.append(RedFlag(
            "incomplete_input", "high",
            f"原始資料有 {rejected_rows} 列被拒絕載入。保留下來的交易可能不是完整樣本，"
            "目前只能做描述性閱讀，不得據此認證正優勢。",
        ))
    if duplicate_rows:
        flags.append(RedFlag(
            "suspected_exact_duplicates", "high",
            f"偵測到 {duplicate_rows} 列完整且時間已知的精確重複交易。"
            "工具不會自行刪除或猜測哪列是真的；在來源釐清前不得認證正優勢。",
        ))

    reasons: list[str] = []
    advice: list[str] = []

    high_flags = [f for f in flags if f.severity == "high"]

    # ── 決策樹(由最嚴重往下判斷)──────────────────────────

    # 0. 完整性保留 → 被拒列或精確重複都可能選擇性扭曲樣本。
    #    不自行刪除、不把 retained-only 統計升格為優勢，也不進樣本外切分。
    if rejected_rows or duplicate_rows:
        level = VerdictLevel.INSUFFICIENT
        discourage = True
        parts = []
        if rejected_rows:
            parts.append(f"{rejected_rows} 列被拒絕")
        if duplicate_rows:
            parts.append(f"{duplicate_rows} 列疑似精確重複")
        issue_text = "、".join(parts)
        headline = (
            f"⚠️ 資料完整性未通過（{issue_text}）：目前僅能把保留列當描述性樣本，"
            "不能判定存在可重複優勢。"
        )
        if rejected_rows:
            reasons.append(
                f"載入時拒絕了 {rejected_rows} 列。被拒列可能與盈虧相關，"
                "只看成功保留的列會產生選擇偏差。"
            )
            for reason in tuple(getattr(log, "rejected_row_reasons", ()))[:3]:
                reasons.append(f"拒絕原因：{reason}")
        if duplicate_rows:
            reasons.append(
                f"完整欄位與進出場時間相同的交易有 {duplicate_rows} 個重複列；"
                "未經來源核對前，重複列可能人為放大樣本數與顯著性。"
            )
        reasons.append(
            "因此以下數字只描述目前保留下來的列，不代表完整紀錄，也不能用來解鎖真錢階段。"
        )
        advice += [
            "回到原始對帳單逐列修正被拒資料，並核對精確重複列是否為不同成交；不要先刪掉虧損列。",
            "資料完整性恢復後再重新分析；在此之前只做紙上模擬。",
        ]

    # A. 樣本不足 → 不論帳面好壞,都先承認「還不知道」。
    #    這必須排在負期望之前:小樣本的負期望同樣可能只是運氣,
    #    若直接判「賭博、方向錯誤、立刻停止」會與「樣本不足無法判斷」
    #    的原則自相矛盾,且過度武斷。樣本足夠的負期望才判賭博(分支 B)。
    elif m.total_trades < min_trades:
        level = VerdictLevel.INSUFFICIENT
        discourage = True   # 樣本不足時也該勸阻「重押」
        if m.expectancy < 0:
            headline = (
                f"⚠️ 樣本不足({m.total_trades} 筆):目前帳面為負"
                f"(每筆 {m.expectancy:+.2f}),但樣本太少,還無法斷定是方法錯還是運氣差。"
            )
            reasons.append(
                f"目前只有 {m.total_trades} 筆交易,帳面期望值為負。"
                "但在這個樣本量下,負期望同樣可能只是隨機的壞運,尚不足以定論。"
            )
        else:
            headline = (
                f"⚠️ 樣本不足({m.total_trades} 筆,建議至少 {max(min_trades, req)} 筆):"
                "現在還無法區分你是有本事,還是運氣好。"
            )
            reasons.append(
                f"目前只有 {m.total_trades} 筆交易。在這個樣本量下,"
                "再漂亮的勝率與獲利,都可能只是隨機波動。"
            )
        advice += [
            f"先用紙上模擬累積到約 {max(min_trades, req)} 筆連續、未挑選的已平倉交易;"
            "在證據足夠前不要投入真錢。",
            "把每一筆交易的『進場理由』記錄下來(用 tag 欄位),日後才能驗證是哪套邏輯有效。",
        ]

    # B. 樣本足夠 + 負期望值 → 判定賭博,無條件勸退
    elif m.expectancy < 0:
        level = VerdictLevel.GAMBLING
        discourage = True
        headline = "⛔ 這是賭博:你的策略樣本期望值為負 —— 方法不變,長期繼續的統計預期就是虧損。"
        reasons.append(
            f"在 {m.total_trades} 筆(已達判定門檻)交易下,每筆平均損益為 "
            f"{m.expectancy:+.2f}(已含成本)。正期望值是任何可持續策略的最低門檻,"
            "而你目前是負的。"
        )
        # 誠實聲明:負的「樣本」期望值同樣有抽樣不確定性 —— 勸退是保守
        # 原則(負值先停手),不是宣稱已在統計上證明你必輸。
        if sig.ci_high > 0:
            reasons.append(
                f"註:平均損益的 95% 信賴區間為 [{sig.ci_low:.2f}, {sig.ci_high:.2f}],"
                "上界仍在 0 以上 —— 負期望的估計也有不確定性。"
                "勸退依據的是保守原則(期望值為負就先停手),"
                "不是「統計上已證明你必輸」。"
            )
        if sig.ci_high > 0:
            # CI 跨 0:證據還沒把負期望釘死 —— 勸退力度不減,但措辭不定罪
            advice += [
                "立刻停止用真金白銀執行這套方法 —— 在證據釐清之前,先停手是唯一穩妥的選擇。",
                "目前的證據不足以把過去的獲利歸因於『可重複的優勢』;先別把它當本事。",
                "回到紙上模擬,先找到『期望值為正』的進出場規則,再談下一步。",
            ]
        else:
            advice += [
                "立刻停止用真金白銀執行這套方法 — 它不是『還沒成功』,而是『方向錯誤』。",
                "帳面曾經賺錢的部分,統計上更像運氣而非本事;運氣會均值回歸。",
                "回到紙上模擬,先找到『期望值為正』的進出場規則,再談下一步。",
            ]

    # C. 樣本夠,但統計檢定過不了 → 帳面賺錢疑似運氣
    elif not sig.is_significant:
        level = VerdictLevel.LUCK_SUSPECTED
        discourage = True
        # 措辭跟著數字走:期望值恰為 0 時說「帳面上賺錢」是說假話
        if m.expectancy > 0:
            headline = (
                "🎲 高度存疑:你帳面上賺錢,但統計檢定無法排除『這只是運氣』的可能。"
            )
        else:
            # 不變量:分支 B 已攔截 expectancy < 0,能到這裡的非正值只有 0
            headline = (
                "🎲 沒有優勢跡象:你目前恰好打平(期望值 0),"
                "統計上更無法主張存在正優勢 —— 扣掉沒算到的成本,很可能其實是負。"
            )
        reasons.append(
            f"平均每筆損益的 bootstrap p 值為 {sig.p_value_bootstrap:.3f}"
            f"(t 檢定 p={sig.p_value_t:.3f}),未達顯著(需 < 0.05)。"
        )
        # CI 涵蓋 0 要「檢查後才說」:單尾 p 與雙尾 CI 口徑不同,
        # 存在 p 不顯著但 CI 全正的組合 —— 硬寫「涵蓋 0」就是說假話
        if sig.ci_low <= 0 <= sig.ci_high:
            reasons.append(
                f"平均損益的 95% 信賴區間為 [{sig.ci_low:.2f}, {sig.ci_high:.2f}]"
                " — 區間涵蓋 0,代表真實期望值有可能根本不為正。"
            )
        else:
            reasons.append(
                f"平均損益的 95% 信賴區間為 [{sig.ci_low:.2f}, {sig.ci_high:.2f}]。"
                "區間雖未涵蓋 0,但裁決以 bootstrap 單尾 p 值(未達顯著)從嚴認定 —— "
                "兩種統計口徑不一致時,本工具一律取保守的一邊。"
            )
        advice += [
            ("別把這段獲利當成『驗證成功』。在統計上,它和『運氣好』無法區分。"
             if m.expectancy > 0 else
             "連帳面獲利都還沒有 —— 別把『沒賠』誤讀成『安全』。"),
            "繼續累積樣本,並嚴格執行同一套規則,看顯著性是否隨樣本增加而成立。",
            "倖存者偏差提醒:你只看到自己這次賺了,沒看到無數用同樣方法賠光退場的人。",
        ]

    # D. 統計顯著,但有高風險警訊或安全邊際過薄 → 脆弱優勢。
    #    thin_edge_margin 的定義就是「勝率小幅下滑就翻負」—— 這正是
    #    FRAGILE_EDGE 存在的理由,若不降級,「脆弱」只是空話。
    elif high_flags or any(f.code == "thin_edge_margin" for f in flags):
        level = VerdictLevel.FRAGILE_EDGE
        discourage = True
        headline = (
            "🟡 優勢脆弱:統計上看似有效,但存在嚴重結構性風險,隨時可能崩潰。"
        )
        reasons.append("平均期望值通過了統計顯著性檢定,代表可能存在真實優勢。")
        reasons.append("但偵測到高嚴重度警訊或安全邊際過薄(見下方),"
                       "這類結構往往『贏到一半才爆』。")
        advice += [
            "先解決上述結構性警訊(高嚴重度警訊或過薄的安全邊際),再考慮放大部位。",
            "做樣本外回測(用本工具的 backtest 模組)驗證優勢是否延續到沒看過的資料。",
        ]

    # E. 統計顯著且無高風險警訊 → 具統計優勢(但仍非保證)
    else:
        level = VerdictLevel.STATISTICAL_EDGE
        discourage = False
        headline = (
            "✅ 具統計優勢:在現有樣本下,你的正期望值通過了顯著性檢定。"
        )
        reasons.append(
            f"平均每筆損益 {sig.mean:+.2f},bootstrap p={sig.p_value_bootstrap:.3f}、"
            f"t 檢定 p={sig.p_value_t:.3f},雙雙顯著。"
        )
        # is_significant 只要求單尾 p_boot < 0.05,而 ci_low 是雙尾 2.5% 分位:
        # 當 0.025 <= p_boot < 0.05 時 ci_low 可能為負。不能硬寫「完全落在 0 以上」,
        # 否則會與報告同頁印出的負下界直接矛盾(對使用者說假話)。
        if sig.ci_low > 0:
            reasons.append(
                f"信賴區間 [{sig.ci_low:.2f}, {sig.ci_high:.2f}] 完全落在 0 以上。"
            )
        else:
            reasons.append(
                f"但 95% 信賴區間 [{sig.ci_low:.2f}, {sig.ci_high:.2f}] 的下界仍略低於 0"
                "(p 值接近顯著門檻)—— 證據屬『邊際等級』,需再累積樣本鞏固。"
            )
        advice += [
            "這是『目前為止』的證據,不是未來的保證。市場會變,優勢會衰減。",
            "持續監控:若新交易讓顯著性掉回不顯著,代表優勢可能正在消失。",
            "務必做樣本外回測,並用本工具產生策略骨架,把規則固化下來避免情緒干擾。",
        ]

    # 中低嚴重度警訊也一併附上(即使已是優勢等級)
    if level == VerdictLevel.STATISTICAL_EDGE and flags:
        advice.append("注意:雖判定為具優勢,仍有以下警訊值得改善 — 見 red_flags。")

    return Verdict(
        level=level,
        should_discourage=discourage,
        headline=headline,
        metrics=m,
        significance=sig,
        required_trades=max(min_trades, req),
        red_flags=flags,
        reasons=reasons,
        advice=advice,
    )
