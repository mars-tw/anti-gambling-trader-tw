"""高階編排:一行呼叫就跑完整套分析流程。

這是其他程式(CLI、Claude Code Skill)最常用的進入點。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .backtest.validate import OutOfSampleReport, walk_forward_validate
from .ingest.loader import load_trades
from .metrics.performance import PerformanceMetrics, compute_metrics
from .models import Market, TradeLog
from .report import render_text_report
from .strategy.profiler import StrategyProfile, profile_strategy
from .strategy.skeleton import generate_skeleton
from .verdict.judge import Verdict, judge


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

    def as_dict(self) -> dict:
        return {
            "source": self.log.source,
            "markets": sorted(m.value for m in self.log.markets),
            "verdict": self.verdict.as_dict(),
            "profile": self.profile.as_dict(),
            "out_of_sample": self.out_of_sample.as_dict(),
        }


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
    oos = walk_forward_validate(log)
    text = render_text_report(log, metrics, verdict, profile, oos)
    code = generate_skeleton(profile, verdict, framework=framework)
    return AnalysisResult(
        log=log,
        metrics=metrics,
        verdict=verdict,
        profile=profile,
        out_of_sample=oos,
        text_report=text,
        strategy_code=code,
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
