"""轉正數字(break-even targets)。

把定性的勸告變成可執行的具體目標:
「你的勝率要從 38% 提高到 45%,或盈虧比從 0.6 拉到 1.6,期望值才會轉正。」

期望值公式(見 performance.py):
    E = 勝率 × 平均獲利 − 敗率 × 平均虧損

固定其中一個變數、解另一個讓 E = 0,就得到「轉正門檻」。
"""

from __future__ import annotations

from dataclasses import dataclass

from .performance import PerformanceMetrics


@dataclass
class BreakEvenTargets:
    """讓期望值轉正(或維持為正)所需的目標值。"""

    current_win_rate: float
    current_payoff_ratio: float
    current_expectancy: float

    # 固定盈虧比,要轉正所需的最低勝率(None 表示無解 / 已轉正)
    required_win_rate: float | None = None
    win_rate_gap: float | None = None         # 與現況差距(百分點)

    # 固定勝率,要轉正所需的最低盈虧比
    required_payoff_ratio: float | None = None
    payoff_gap: float | None = None

    # 若每筆少付多少成本就能轉正(金額)
    fee_cut_to_breakeven: float | None = None

    already_positive: bool = False
    structurally_hard: bool = False           # 光調單一變數救不了
    messages: list[str] = None                # type: ignore

    def __post_init__(self) -> None:
        if self.messages is None:
            self.messages = []


def compute_break_even(metrics: PerformanceMetrics) -> BreakEvenTargets:
    """計算轉正所需的勝率 / 盈虧比 / 成本削減目標。"""
    win_rate = metrics.win_rate
    avg_win = metrics.avg_win
    avg_loss = metrics.avg_loss      # 已是正值
    payoff = metrics.payoff_ratio
    expectancy = metrics.expectancy

    t = BreakEvenTargets(
        current_win_rate=win_rate,
        current_payoff_ratio=payoff,
        current_expectancy=expectancy,
    )

    if expectancy > 0:
        t.already_positive = True
        t.messages.append("你的期望值已經為正 —— 目標是『維持』,別讓它衰退。")
        return t

    # ── 固定盈虧比,要轉正所需的最低勝率 ──
    # E = 0 → win_rate* × avg_win = (1 − win_rate*) × avg_loss
    #     → win_rate* = avg_loss / (avg_win + avg_loss)
    # 實務上勝率很難超過 ~90%;若所需勝率高到不切實際,當作結構性無解,
    # 別給「衝到 99% 勝率」這種誤導目標。
    if avg_win + avg_loss > 0:
        req_wr = avg_loss / (avg_win + avg_loss)
        if req_wr < 0.9:
            t.required_win_rate = req_wr
            t.win_rate_gap = req_wr - win_rate
            t.messages.append(
                f"維持現在的盈虧比({payoff:.2f}),勝率要從 {win_rate:.0%} "
                f"提高到 {req_wr:.0%}(差 {t.win_rate_gap * 100:.0f} 個百分點)才會轉正。"
            )
        else:
            # 需要過高勝率才轉正(≥90%)→ 結構性無解
            t.structurally_hard = True
            t.messages.append(
                f"以你目前的盈虧比,要轉正得有近 {req_wr:.0%} 的勝率,實務上幾乎不可能 —— "
                "問題出在『賺太少賠太多』的結構,必須提高盈虧比(減少虧損、增加獲利)。"
            )

    # ── 固定勝率,要轉正所需的最低盈虧比 ──
    # E = 0 → win_rate × avg_win = (1 − win_rate) × avg_loss
    #     → payoff* = avg_win/avg_loss = (1 − win_rate)/win_rate
    if win_rate > 0:
        req_payoff = (1 - win_rate) / win_rate
        t.required_payoff_ratio = req_payoff
        t.payoff_gap = req_payoff - payoff
        if payoff > 0:
            t.messages.append(
                f"維持現在的勝率({win_rate:.0%}),盈虧比要從 {payoff:.2f} "
                f"拉到 {req_payoff:.2f}(差 {t.payoff_gap:.2f})才會轉正 —— "
                "也就是讓平均獲利更大、或平均虧損更小。"
            )
    else:
        t.structurally_hard = True
        t.messages.append("你幾乎沒有獲利交易,光調盈虧比救不了 —— 進場規則本身要重做。")

    # ── 若每筆少付多少成本就轉正 ──
    # 期望值為負時,看「平均每筆成本」是否大於虧損缺口。
    # 用『金額』表達(明確),不用相對 avg_fee 的比例(因 avg_fee 含稅與滑價,
    # 講「降低 X% 成本」會讓使用者誤以為只要降手續費率)。
    if metrics.total_trades > 0:
        avg_fee = metrics.total_fees / metrics.total_trades
        if avg_fee > 0 and (expectancy + avg_fee) > 0:
            needed = -expectancy            # 每筆要補的缺口(金額)
            t.fee_cut_to_breakeven = needed
            t.messages.append(
                f"你的每筆平均成本約 {avg_fee:.2f},而期望值只差 {needed:.2f} 就轉正 —— "
                f"只要每筆能省下約 {needed:.2f} 的成本(換低費率券商、降低交易頻率),"
                "就可能由負翻正。問題可能不在策略,而在被成本吃掉。"
            )

    return t
