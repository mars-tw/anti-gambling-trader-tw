"""樣本外驗證 — 揭穿過度配適與倖存者偏差的利器。

很多策略「在歷史資料上很賺」,只是因為它被刻意調整到剛好貼合那段歷史
(過度配適 / overfitting)。要戳破這種假象,最直接的方法是:

    把交易紀錄依時間切成「前段(樣本內)」與「後段(樣本外)」,
    看前段展現的優勢,在後段是否仍然存在。

如果優勢在樣本外消失,那它很可能是雜訊或過度配適 —— 也就是說,
過去的獲利更可能是運氣,而非可延續到未來的真本事。

注意:這裡驗證的是「使用者實際交易紀錄」本身的時間穩定度,
不需要外部行情資料,因此對任何市場、任何資料來源都適用。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from ..markets import infer_pnl_currency
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
    available: bool = True
    unavailable_reason: str = ""

    def as_dict(self) -> dict:
        def seg(s: SegmentResult) -> dict:
            return {
                "label": s.label,
                "n_trades": s.n_trades,
                "win_rate": s.win_rate,
                "expectancy": s.expectancy,
                "profit_factor": s.profit_factor,
                "total_pnl": s.total_pnl,
                "p_value_t": s.significance.p_value_t,
                "p_value_bootstrap": s.significance.p_value_bootstrap,
                "is_significant": s.significance.is_significant,
            }
        return {
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
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


def holdout_validate(
    log: TradeLog,
    *,
    split_ratio: float = 0.7,
    n_bootstrap: int = 3000,
) -> OutOfSampleReport:
    """依時間做**單一切點**的樣本內 / 樣本外驗證,比較優勢是否延續。

    命名說明:這是「單一時序 holdout」,**不是**滾動式 walk-forward
    (多折前進驗證)。舊名 walk_forward_validate 名實不符,已改名;
    舊名保留為 deprecated alias。

    為何不做真正的多折滾動:本工具的典型樣本只有 30~60 筆,切成多折後
    每折僅約 10 筆,顯著性檢定幾乎必然失效(實測每折樣本外顯著率僅 15~25%),
    反而製造大量假陰性。單一 holdout 在此樣本規模下是較誠實的選擇。

    Args:
        log:         交易紀錄
        split_ratio: 前段(樣本內)佔比,預設 0.7
        n_bootstrap: bootstrap 次數

    Returns:
        OutOfSampleReport
    """
    if not 0 < split_ratio < 1:
        raise ValueError(f"split_ratio 必須介於 0 與 1 之間,收到 {split_ratio}")

    # TradeLog.trades 是公開可變 list；切分前重新檢查，不能讓建構後加入的
    # 重複列繞過完整性保留而被分成看似獨立的樣本內／外資料。
    log.refresh_integrity()
    raw = list(log)
    n = len(raw)
    interp: list[str] = []

    rejected_rows = int(getattr(log, "rejected_row_count", 0) or 0)
    duplicate_rows = int(getattr(log, "suspected_duplicate_count", 0) or 0)
    if rejected_rows or duplicate_rows:
        issues: list[str] = []
        if rejected_rows:
            issues.append(f"{rejected_rows} 列被拒絕載入")
        if duplicate_rows:
            issues.append(f"{duplicate_rows} 列疑似精確重複")
        issue_text = "、".join(issues)
        empty_sig = SignificanceResult(0, 0, 0, 0, 1.0, 1.0, 0, 0, False)
        seg = SegmentResult("資料完整性未通過", n, 0, 0, 0, 0, empty_sig)
        return OutOfSampleReport(
            in_sample=seg,
            out_sample=seg,
            edge_persisted=False,
            degradation=1.0,
            headline=f"⚠️ 資料完整性未通過（{issue_text}），不執行樣本外切分。",
            interpretation=[
                "被拒列可能造成選擇偏差，精確重複列也可能放大樣本數與顯著性。",
                "工具保留問題計數、不自行刪除或去重；請先回到原始對帳單逐列核對。",
            ],
            available=False,
            unavailable_reason=f"資料完整性未通過：{issue_text}",
        )

    # 只要混入一筆未知出場時間,整份紀錄就沒有可信的完整時序。
    # 不可靜默丟掉未知列(會改變樣本),也不可拿 placeholder 排在最前面後硬切。
    # getattr 保留對舊版/外部自訂 Trade-like 物件的相容性。
    if any(not getattr(t, "exit_time_known", True) for t in raw):
        empty_sig = SignificanceResult(0, 0, 0, 0, 1.0, 1.0, 0, 0, False)
        seg = SegmentResult("出場時間不完整", n, 0, 0, 0, 0, empty_sig)
        return OutOfSampleReport(
            in_sample=seg,
            out_sample=seg,
            edge_persisted=False,
            degradation=1.0,
            headline="⚠️ 部分交易缺少真實出場時間,無法做時序樣本外驗證。",
            interpretation=[
                "樣本外驗證必須知道每筆交易何時結束;用進場時間或預設日期代替會造成前視偏差。",
                "本工具不會刪掉缺時間的交易後假裝完成驗證;請補齊 exit_time 再重跑。",
            ],
            available=False,
            unavailable_reason="部分交易缺少可靠的出場時間",
        )

    # 樣本內/外的 expectancy 是「金額」，切分前必須先確認整份紀錄使用
    # 同一幣別。若先各自 compute，前段 USD、後段 TWD 會被當成相同單位
    # 直接比較，甚至可能錯報優勢延續。
    currencies: set[str] = set()
    unresolved_currency = 0
    for trade in raw:
        currency = getattr(trade, "pnl_currency", None)
        if not currency:
            currency = infer_pnl_currency(trade.symbol, trade.market)
        if currency:
            currencies.add(str(currency).upper())
        else:
            unresolved_currency += 1
    if len(currencies) > 1 or unresolved_currency:
        shown = "、".join(sorted(currencies)) or "未知"
        empty_sig = SignificanceResult(0, 0, 0, 0, 1.0, 1.0, 0, 0, False)
        seg = SegmentResult("損益幣別不可比", n, 0, 0, 0, 0, empty_sig)
        return OutOfSampleReport(
            in_sample=seg,
            out_sample=seg,
            edge_persisted=False,
            degradation=1.0,
            headline="⚠️ 損益幣別不同或不明，無法比較樣本內/外期望值。",
            interpretation=[
                f"偵測到幣別 {shown}（不明 {unresolved_currency} 筆）；"
                "不同幣別金額沒有匯率與同一結算基準時不能直接相減或比較。",
                "請依帳戶結算幣別分檔，或先用可稽核匯率換成同一幣別。",
            ],
            available=False,
            unavailable_reason="樣本內外的損益幣別不同或不明",
        )

    awareness = {
        bool(t.exit_time.tzinfo is not None and t.exit_time.utcoffset() is not None)
        for t in raw
    }
    if len(awareness) > 1:
        empty_sig = SignificanceResult(0, 0, 0, 0, 1.0, 1.0, 0, 0, False)
        seg = SegmentResult("時區基準不一致", n, 0, 0, 0, 0, empty_sig)
        return OutOfSampleReport(
            in_sample=seg,
            out_sample=seg,
            edge_persisted=False,
            degradation=1.0,
            headline="⚠️ 出場時間混用有時區與無時區格式,無法安全排序做樣本外驗證。",
            interpretation=[
                "請先把所有 exit_time 統一成同一 UTC offset，或全部改成同一當地時區。"
            ],
            available=False,
            unavailable_reason="出場時間的時區基準不一致",
        )

    ordered = list(log.sorted_by_time())

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
            available=False,
            unavailable_reason="交易筆數不足，切分後每段少於 10 筆",
        )

    target_split = max(10, int(n * split_ratio))
    target_split = min(target_split, n - 10)  # 確保兩段各至少 10 筆

    # 不可把同一個出場時間的交易拆到樣本內與樣本外。那通常是同一事件的
    # 多筆單;拆開會讓後段偷看到前段同一時點的資訊。更重要的是,pnl-only
    # 檔案若沒有時間,loader 會把全部交易標成同一預設時間;此時按列順序
    # 硬切並稱為「時序樣本外」是假的驗證,必須誠實拒絕。
    valid_splits = [
        i for i in range(10, n - 9)
        if ordered[i - 1].exit_time < ordered[i].exit_time
    ]
    if not valid_splits:
        empty_sig = SignificanceResult(0, 0, 0, 0, 1.0, 1.0, 0, 0, False)
        seg = SegmentResult("缺少可切分時間", n, 0, 0, 0, 0, empty_sig)
        return OutOfSampleReport(
            in_sample=seg,
            out_sample=seg,
            edge_persisted=False,
            degradation=1.0,
            headline="⚠️ 缺少可切分的出場時間,無法做時序樣本外驗證。",
            interpretation=[
                "交易的出場時間全部相同,或任何時間邊界都無法讓前後段各保留至少 10 筆。",
                "本工具不會用檔案列順序冒充時間順序;請補上真實 exit_time 後再驗證。",
            ],
            available=False,
            unavailable_reason="沒有可維持前後段各至少 10 筆的時間切點",
        )

    split = min(valid_splits, key=lambda i: (abs(i - target_split), i))

    in_log = TradeLog(ordered[:split], log.source, log.account_label + "::in")
    out_log = TradeLog(ordered[split:], log.source, log.account_label + "::out")

    in_seg = _summarize(in_log, "樣本內(前段)", n_bootstrap)
    out_seg = _summarize(out_log, "樣本外(後段)", n_bootstrap)

    # 優勢是否延續:樣本內必須先有顯著正期望,樣本外期望值仍為正、
    # 衰退不過大,且樣本外本身也要『統計顯著』。若前段從未建立優勢,
    # 後段再漂亮也只能算新的探索性訊號,不能倒推成「原有優勢延續」。
    # (這是修正:原本只看期望值方向與衰減,沒檢查樣本外顯著性,
    #  會把 10~15 筆剛好為正但 p 值很高的結果誤報成『優勢延續』。)
    degradation = 1.0
    if in_seg.expectancy != 0:
        degradation = (in_seg.expectancy - out_seg.expectancy) / abs(in_seg.expectancy)

    edge_persisted = (
        in_seg.expectancy > 0
        and in_seg.significance.is_significant
        and out_seg.expectancy > 0
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
    elif not in_seg.significance.is_significant:
        headline = "⚠️ 樣本內未確認:前段帳面雖為正,但統計上無法排除只是運氣。"
        interp += [
            f"樣本內期望值 {in_seg.expectancy:+.2f},但未通過顯著性檢定"
            f"(bootstrap p={in_seg.significance.p_value_bootstrap:.3f}、"
            f"t p={in_seg.significance.p_value_t:.3f})。",
            "前段尚未建立可供『延續』驗證的優勢,因此即使後段表現較好,"
            "也不能倒過來宣稱原有優勢通過樣本外驗證。",
            "後段結果只能視為新的探索性證據;應先固定規則,再用下一段完全沒看過的資料驗證。",
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

    # 誠實前提(第 9 輪外部審查):「樣本外」只對「規則在前段就定好」的
    # 情況成立。若使用者的規則是看著**全部**歷史調出來的,後段其實也被
    # 看過 —— 這個驗證會高估可信度。必須明講,不能讓人誤以為是鐵律。
    interp.append(
        "前提提醒:此驗證假設你的規則沒有「看著後段資料」調整過。"
        "若你是看完整段歷史才定規則,後段對你並不是真正沒看過的資料,"
        "這裡的結論會偏樂觀。"
    )

    return OutOfSampleReport(
        in_sample=in_seg,
        out_sample=out_seg,
        edge_persisted=edge_persisted,
        degradation=degradation,
        headline=headline,
        interpretation=interp,
    )


def walk_forward_validate(*args, **kwargs) -> OutOfSampleReport:
    """已棄用:請改用 holdout_validate()。

    舊名暗示這是滾動式 walk-forward,但實作為單一時序 holdout,名實不符。
    """
    warnings.warn(
        "walk_forward_validate() 已棄用,請改用 holdout_validate()。"
        "舊名暗示滾動式多折驗證,實際為單一時序 holdout。",
        DeprecationWarning,
        stacklevel=2,
    )
    return holdout_validate(*args, **kwargs)
