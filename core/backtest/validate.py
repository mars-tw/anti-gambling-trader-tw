"""樣本外驗證 — 揭穿過度配適與倖存者偏差的利器。

很多策略「在歷史資料上很賺」,只是因為它被刻意調整到剛好貼合那段歷史
(過度配適 / overfitting)。要戳破這種假象,最直接的方法是:

    把交易紀錄依時間切成「前段(樣本內)」與「後段(樣本外)」,
    看前段展現的優勢,在後段是否仍然存在。

如果優勢在樣本外消失,那它八成是雜訊或過度配適 —— 也就是說,
過去的獲利更可能是運氣,而非可延續到未來的真本事。

注意:這裡驗證的是「使用者實際交易紀錄」本身的時間穩定度,
不需要外部行情資料,因此對任何市場、任何資料來源都適用。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..metrics.performance import PerformanceMetrics, compute_metrics
from ..models import TradeLog
from ..verdict.statistics import SignificanceResult, test_expectancy_positive


@dataclass
class SegmentResult:
    """單一時間區段的績效摘要。"""

    label: str
    n_trades: int
    win_rate: float
    expectancy: float
    profit_factor: float
    total_pnl: float
    significance: SignificanceResult


@dataclass
class OutOfSampleReport:
    """樣本內 / 樣本外比較報告。"""

    in_sample: SegmentResult
    out_sample: SegmentResult
    edge_persisted: bool          # 優勢是否延續到樣本外
    degradation: float            # 期望值衰減比例(正=變差)
    headline: str
    interpretation: list[str]

    def as_dict(self) -> dict:
        def seg(s: SegmentResult) -> dict:
            return {
                "label": s.label,
                "n_trades": s.n_trades,
                "win_rate": s.win_rate,
                "expectancy": s.expectancy,
                "profit_factor": s.profit_factor,
                "total_pnl": s.total_pnl,
                "p_value_bootstrap": s.significance.p_value_bootstrap,
                "is_significant": s.significance.is_significant,
            }
        return {
            "in_sample": seg(self.in_sample),
            "out_sample": seg(self.out_sample),
            "edge_persisted": self.edge_persisted,
            "degradation": self.degradation,
            "headline": self.headline,
            "interpretation": self.interpretation,
        }


def _summarize(log: TradeLog, label: str, n_bootstrap: int) -> SegmentResult:
    m = compute_metrics(log)
    pnls = [t.pnl or 0.0 for t in log]
    sig = test_expectancy_positive(pnls, n_bootstrap=n_bootstrap)
    return SegmentResult(
        label=label,
        n_trades=m.total_trades,
        win_rate=m.win_rate,
        expectancy=m.expectancy,
        profit_factor=m.profit_factor,
        total_pnl=m.total_pnl,
        significance=sig,
    )


def _degr_word(degradation: float) -> str:
    """把衰減比例轉成自然語句(負值代表樣本外反而更好)。"""
    if degradation < 0:
        return f"不減反增 {abs(degradation):.0%}"
    return f"衰減 {degradation:.0%}"


def walk_forward_validate(
    log: TradeLog,
    *,
    split_ratio: float = 0.7,
    n_bootstrap: int = 3000,
) -> OutOfSampleReport:
    """依時間把交易切成樣本內 / 樣本外,比較優勢是否延續。

    Args:
        log:         交易紀錄
        split_ratio: 前段(樣本內)佔比,預設 0.7
        n_bootstrap: bootstrap 次數

    Returns:
        OutOfSampleReport
    """
    ordered = list(log.sorted_by_time())
    n = len(ordered)
    interp: list[str] = []

    if n < 20:
        # 樣本太少,切兩半後每段都不可靠
        empty_sig = SignificanceResult(0, 0, 0, 0, 1.0, 1.0, 0, 0, False)
        seg = SegmentResult("樣本不足", n, 0, 0, 0, 0, empty_sig)
        return OutOfSampleReport(
            in_sample=seg,
            out_sample=seg,
            edge_persisted=False,
            degradation=1.0,
            headline="⚠️ 交易筆數太少(< 20),無法做有意義的樣本外驗證。",
            interpretation=[
                "切成樣本內/外後每段都太小,任何結論都不可靠。",
                "請先累積更多交易紀錄,再回來做這項驗證。",
            ],
        )

    split = max(10, int(n * split_ratio))
    split = min(split, n - 10)  # 確保兩段各至少 10 筆

    in_log = TradeLog(ordered[:split], log.source, log.account_label + "::in")
    out_log = TradeLog(ordered[split:], log.source, log.account_label + "::out")

    in_seg = _summarize(in_log, "樣本內(前段)", n_bootstrap)
    out_seg = _summarize(out_log, "樣本外(後段)", n_bootstrap)

    # 優勢是否延續:樣本外期望值仍為正、衰退不過大,
    # 且樣本外本身要『統計顯著』 —— 否則樣本外那段正期望可能只是運氣。
    # (這是修正:原本只看期望值方向與衰減,沒檢查樣本外顯著性,
    #  會把 10~15 筆剛好為正但 p 值很高的結果誤報成『優勢延續』。)
    degradation = 1.0
    if in_seg.expectancy != 0:
        degradation = (in_seg.expectancy - out_seg.expectancy) / abs(in_seg.expectancy)

    edge_persisted = (
        out_seg.expectancy > 0
        and degradation < 0.5
        and out_seg.significance.is_significant
    )

    # ── 解讀 ──
    if in_seg.expectancy <= 0:
        headline = "🎲 連樣本內都沒有正期望值 —— 這份紀錄看不出任何可延續的優勢。"
        interp += [
            "前段本身就不賺錢,談不上『優勢延續』的問題。",
            "目前的證據比較支持『這是賭博/虧損策略』而非『有方法』。",
        ]
    elif edge_persisted:
        headline = "✅ 優勢延續:樣本內展現的正期望值,在樣本外仍然存在且統計顯著。"
        interp += [
            f"樣本內期望值 {in_seg.expectancy:+.2f} → 樣本外 {out_seg.expectancy:+.2f}"
            f"({_degr_word(degradation)}),仍維持正值且通過顯著性檢定。",
            "這是相對強的證據:優勢不只是貼合舊資料,在沒看過的後段也成立。",
            "但仍非保證 —— 市場結構改變時,優勢可能在未來才衰減。",
        ]
    elif out_seg.expectancy > 0 and not out_seg.significance.is_significant:
        # 樣本外帳面為正但統計不顯著 —— 不能當成優勢延續
        headline = "⚠️ 樣本外不顯著:後段帳面雖為正,但統計上無法排除只是運氣。"
        interp += [
            f"樣本內期望值 {in_seg.expectancy:+.2f} → 樣本外 {out_seg.expectancy:+.2f},"
            f"但樣本外只有 {out_seg.n_trades} 筆,p 值未達顯著。",
            "後段樣本太少,正期望可能純屬巧合,不足以證明優勢延續到未來。",
        ]
    else:
        headline = "⚠️ 優勢消失:樣本內看似有效,但在樣本外大幅衰退或翻負。"
        interp += [
            f"樣本內期望值 {in_seg.expectancy:+.2f} → 樣本外 {out_seg.expectancy:+.2f}"
            f"({_degr_word(degradation)})。",
            "這是過度配適 / 倖存者偏差的典型徵兆:策略只是『記住了』舊行情,",
            "面對新資料就失靈。把這種策略自動化,等於把運氣當實力下注。",
        ]

    return OutOfSampleReport(
        in_sample=in_seg,
        out_sample=out_seg,
        edge_persisted=edge_persisted,
        degradation=degradation,
        headline=headline,
        interpretation=interp,
    )
