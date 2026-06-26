"""把分析結果彙整成一份完整、誠實、好讀的中文報告。"""

from __future__ import annotations

from .antiscam.signals import scam_warnings_for
from .backtest.validate import OutOfSampleReport
from .metrics.performance import PerformanceMetrics
from .models import TradeLog
from .strategy.profiler import StrategyProfile
from .verdict.judge import Verdict, VerdictLevel


def _money(x: float) -> str:
    return f"{x:,.2f}"


def render_text_report(
    log: TradeLog,
    metrics: PerformanceMetrics,
    verdict: Verdict,
    profile: StrategyProfile,
    oos: OutOfSampleReport | None = None,
) -> str:
    """產生純文字報告(適合終端機輸出)。"""
    L: list[str] = []
    L.append("=" * 70)
    L.append("                  反詐投資王 — 交易績效誠實報告")
    L.append("=" * 70)
    L.append(f"資料來源: {log.source}")
    L.append(f"涵蓋市場: {', '.join(sorted(m.value for m in log.markets))}")
    L.append("")

    # ── 一句話裁決 ──
    L.append("【最終裁決】")
    L.append(f"  {verdict.headline}")
    L.append("")

    # ── 核心數字 ──
    m = metrics
    L.append("【核心績效】")
    L.append(f"  交易筆數      : {m.total_trades}(勝 {m.wins} / 負 {m.losses})")
    L.append(f"  勝率          : {m.win_rate:.1%}")
    L.append(f"  盈虧比        : {m.payoff_ratio:.2f}(平均賺 {_money(m.avg_win)} / 平均賠 {_money(m.avg_loss)})")
    L.append(f"  獲利因子      : {m.profit_factor:.2f}")
    L.append(f"  每筆期望值    : {_money(m.expectancy)}  ← 最關鍵的單一數字")
    L.append(f"  總損益        : {_money(m.total_pnl)}(已扣估計成本 {_money(m.total_fees)})")
    L.append(f"  最大回撤      : {_money(m.max_drawdown)}({m.max_drawdown_pct:.1%})")
    L.append(f"  最長連虧      : {m.max_consecutive_losses} 次")
    L.append(f"  夏普 / 索提諾 : {m.sharpe:.2f} / {m.sortino:.2f}(每筆基準,非年化)")
    L.append(f"  最賺一筆佔比  : {m.top_trade_pnl_share:.1%} 的總獲利")
    L.append("")

    # ── 統計顯著性 ──
    sig = verdict.significance
    L.append("【這是優勢,還是運氣?(統計檢定)】")
    L.append(f"  每筆平均損益          : {_money(sig.mean)}")
    L.append(f"  95% 信賴區間          : [{_money(sig.ci_low)}, {_money(sig.ci_high)}]")
    L.append(f"  t 檢定 p 值           : {sig.p_value_t:.4f}")
    L.append(f"  Bootstrap p 值        : {sig.p_value_bootstrap:.4f}")
    verdict_word = "顯著為正(像真優勢)" if sig.is_significant else "不顯著(無法排除是運氣)"
    L.append(f"  結論                  : {verdict_word}")
    # 負期望時,「需要多少樣本」沒有意義(再多樣本也無法把負期望變成優勢),
    # 不應把內部哨兵值(如 9999)直接印給使用者。
    if m.expectancy <= 0:
        L.append(
            "  建議最少交易筆數      : 不適用 — 期望值為負,"
            "問題不在筆數,而在方法;再多交易也無法變成優勢"
        )
    else:
        L.append(f"  建議最少交易筆數      : {verdict.required_trades}(目前 {m.total_trades})")
    L.append("")

    # ── 賭博警訊 ──
    if verdict.red_flags:
        L.append("【偵測到的賭博 / 風險警訊】")
        for rf in verdict.red_flags:
            tag = {"high": "🔴 高", "medium": "🟠 中", "low": "🟡 低"}.get(rf.severity, "•")
            L.append(f"  {tag} {rf.message}")
        L.append("")

    # ── 樣本外驗證 ──
    if oos is not None:
        L.append("【樣本外驗證(揭穿過度配適 / 倖存者偏差)】")
        L.append(f"  {oos.headline}")
        L.append(
            f"  樣本內: {oos.in_sample.n_trades} 筆, 期望值 {_money(oos.in_sample.expectancy)}"
        )
        L.append(
            f"  樣本外: {oos.out_sample.n_trades} 筆, 期望值 {_money(oos.out_sample.expectancy)}"
        )
        for line in oos.interpretation:
            L.append(f"    - {line}")
        L.append("")

    # ── 策略輪廓 ──
    L.append("【你的交易模式(反推)】")
    L.append(f"  風格          : {profile.style}")
    L.append(f"  平均持倉      : {profile.avg_holding_days:.1f} 天")
    L.append(f"  標的集中度    : 前三大標的佔 {profile.symbol_concentration:.0%}(共 {profile.distinct_symbols} 檔)")
    if profile.per_tag_winrate:
        L.append("  各策略標籤勝率:")
        for tag, wr in sorted(profile.per_tag_winrate.items(), key=lambda x: -x[1]):
            cnt = profile.tags.get(tag, 0)
            L.append(f"    - {tag}: 勝率 {wr:.0%}(共 {cnt} 筆)")
    for note in profile.notes:
        L.append(f"  ⚠ {note}")
    L.append("")

    # ── 建議 ──
    L.append("【給你的建議】")
    for a in verdict.advice:
        L.append(f"  • {a}")
    L.append("")

    # ── 反詐警語(若分析結果命中詐騙受害特徵)──
    scam_warnings = scam_warnings_for(metrics, profile)
    if scam_warnings:
        L.append("【🛡 反詐提醒 — 你的交易可能與投資詐騙有關】")
        for w in scam_warnings:
            L.append(f"  {w}")
        L.append("  ── 想進一步檢測是否遇到詐騙,請執行:anti-gambling-trader scam-check")
        L.append("")

    # ── 勸退橫幅(若需要)──
    if verdict.should_discourage:
        L.append("┌" + "─" * 66 + "┐")
        L.append("│  ⛔ 勸退提醒                                                      │")
        L.append("│                                                                  │")
        if verdict.level == VerdictLevel.GAMBLING:
            L.append("│  根據統計分析,你目前的交易行為比較接近『賭博』而非投資。        │")
            L.append("│  期望值為負代表:玩越久、賠越多,這是數學,不是運氣問題。        │")
        elif verdict.level == VerdictLevel.LUCK_SUSPECTED:
            L.append("│  你帳面上賺錢,但統計上無法排除這只是運氣。                      │")
            L.append("│  別讓一時的好運,騙你以為自己找到了穩定獲利的方法。              │")
        elif verdict.level == VerdictLevel.INSUFFICIENT:
            L.append("│  你的交易次數還太少,任何結論(好或壞)都不可信。                │")
            L.append("│  在累積足夠樣本前,請勿放大部位、勿借錢、勿重押。                │")
        else:
            L.append("│  你的策略雖有統計訊號,但結構脆弱、風險偏高。                    │")
            L.append("│  請先修正警訊並通過樣本外驗證,再考慮自動化或加碼。              │")
        L.append("│                                                                  │")
        L.append("│  真正的投資優勢,經得起統計檢定與時間考驗。慢慢來,別賭。        │")
        L.append("└" + "─" * 66 + "┘")
    else:
        L.append("✅ 你的策略目前通過了統計檢定與樣本外驗證,屬於少數具備優勢的情況。")
        L.append("   但請記得:這是『目前』的證據,不是『未來』的保證。持續監控、嚴守紀律。")
    L.append("")
    L.append("─" * 70)
    L.append("免責聲明:本報告為統計分析工具的輸出,僅供教育與研究用途,")
    L.append("不構成任何投資建議。投資有風險,盈虧自負。過去績效不代表未來表現。")
    L.append("─" * 70)
    return "\n".join(L)
