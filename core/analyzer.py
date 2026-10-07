"""高階編排:一行呼叫就跑完整套分析流程。

這是其他程式(CLI、Claude Code Skill)最常用的進入點。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .backtest.validate import OutOfSampleReport, holdout_validate
from .ingest.loader import load_trades
from .metrics.performance import PerformanceMetrics, compute_metrics
from .models import Market, TradeLog
from .report import render_text_report
from .strategy.per_tag import (
    CounterfactualResult,
    FollowGuruResult,
    TagVerdict,
    counterfactual_drop_worst,
    follow_the_guru,
    per_tag_verdicts,
)
from .strategy.profiler import StrategyProfile, profile_strategy
from .strategy.skeleton import generate_skeleton
from .verdict.judge import Verdict, judge


def sanitize_json(obj):
    """遞迴把 inf/-inf/NaN 轉成 None。

    json.dumps 對非有限 float 會輸出非標準的 Infinity/NaN 字面值,
    嚴格解析器直接炸。inf 的語意是「不適用」(如全勝樣本的獲利因子),
    序列化成 null 才誠實。所有對外 JSON 都必須過這一層。
    """
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_json(x) for x in obj]
    return obj


@dataclass
class AnalysisResult:
    """完整分析的所有產出。"""

    log: TradeLog
    metrics: PerformanceMetrics
    verdict: Verdict
    profile: StrategyProfile
    out_of_sample: OutOfSampleReport
    text_report: str
    strategy_code: str
    tag_verdicts: list[TagVerdict] = None             # type: ignore
    counterfactual: CounterfactualResult | None = None
    follow_guru: FollowGuruResult | None = None

    def __post_init__(self) -> None:
        if self.tag_verdicts is None:
            self.tag_verdicts = []

    def as_dict(self) -> dict:
        from .metrics.breakeven import compute_break_even
        from .onboarding import stage_from_analysis

        be = compute_break_even(self.metrics)
        stage = stage_from_analysis(self)
        # 整包過 sanitize_json:inf 不只出現在 metrics(OOS 全勝區段、
        # per-tag 的 profit_factor 同樣會是 inf),單點防護會漏
        return sanitize_json({
            "source": self.log.source,
            "markets": sorted(m.value for m in self.log.markets),
            "integrity": self.log.integrity_as_dict(),
            "verdict": self.verdict.as_dict(),
            "stage": {
                "code": stage.code,
                "title": stage.title,
                "reason": stage.reason,
                "next_actions": list(stage.next_actions),
            },
            "profile": self.profile.as_dict(),
            "out_of_sample": self.out_of_sample.as_dict(),
            # per-tag 為『描述統計』,刻意不含顯著性/優勢等級
            # (多重比較未校正會把運氣誤認為優勢,見 strategy/per_tag.py)
            "tag_verdicts": [
                {
                    "tag": tv.tag, "n_trades": tv.n_trades,
                    "expectancy": tv.expectancy, "total_pnl": tv.total_pnl,
                    "win_rate": tv.win_rate, "profit_factor": tv.profit_factor,
                    "low_sample": tv.low_sample, "is_losing": tv.is_losing,
                }
                for tv in self.tag_verdicts
            ],
            "follow_guru": (
                {
                    "n_trades": self.follow_guru.n_trades,
                    "expectancy": self.follow_guru.expectancy,
                    "total_pnl": self.follow_guru.total_pnl,
                    "level": self.follow_guru.level.value,
                } if self.follow_guru else None
            ),
            "counterfactual": (
                {
                    "worst_tag": self.counterfactual.worst_tag,
                    "before_expectancy": self.counterfactual.before_expectancy,
                    "after_expectancy": self.counterfactual.after_expectancy,
                    "before_total_pnl": self.counterfactual.before_total_pnl,
                    "after_total_pnl": self.counterfactual.after_total_pnl,
                } if self.counterfactual else None
            ),
            "breakeven": {
                "already_positive": be.already_positive,
                "structurally_hard": be.structurally_hard,
                "required_win_rate": be.required_win_rate,
                "required_payoff_ratio": be.required_payoff_ratio,
                "fee_cut_to_breakeven": be.fee_cut_to_breakeven,
            },
        })


def analyze_log(
    log: TradeLog,
    *,
    framework: str = "backtrader",
    n_bootstrap: int = 5000,
) -> AnalysisResult:
    """對一份已載入的 TradeLog 跑完整分析。"""
    metrics = compute_metrics(log)
    verdict = judge(log, metrics=metrics, n_bootstrap=n_bootstrap)
    profile = profile_strategy(log)
    oos = holdout_validate(log, n_bootstrap=n_bootstrap)
    from .onboarding import stage_from_components

    stage = stage_from_components(verdict=verdict, metrics=metrics, oos=oos)

    # 逐策略裁決 + 反事實 + 跟單抽算(實用性核心)
    tag_verdicts = per_tag_verdicts(log)
    counterfactual = counterfactual_drop_worst(log, tag_verdicts=tag_verdicts)
    guru = follow_the_guru(log, n_bootstrap=n_bootstrap)

    text = render_text_report(
        log, metrics, verdict, profile, oos,
        tag_verdicts=tag_verdicts, counterfactual=counterfactual, follow_guru=guru,
    )
    code = generate_skeleton(profile, verdict, framework=framework, stage=stage)
    return AnalysisResult(
        log=log,
        metrics=metrics,
        verdict=verdict,
        profile=profile,
        out_of_sample=oos,
        text_report=text,
        strategy_code=code,
        tag_verdicts=tag_verdicts,
        counterfactual=counterfactual,
        follow_guru=guru,
    )


def analyze_file(
    path: str | Path,
    *,
    market_hint: Market | None = None,
    framework: str = "backtrader",
    auto_estimate_costs: bool = True,
    n_bootstrap: int = 5000,
) -> AnalysisResult:
    """從檔案載入並分析(最常用的一行式進入點)。"""
    log = load_trades(
        path, market_hint=market_hint, auto_estimate_costs=auto_estimate_costs
    )
    return analyze_log(log, framework=framework, n_bootstrap=n_bootstrap)
