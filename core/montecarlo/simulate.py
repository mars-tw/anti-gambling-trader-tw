"""風險情境模擬的實作。"""

from __future__ import annotations

import math
import random
import re
import statistics
import time
from array import array
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..survivorship import prob_streak_in_trials


_CAPITAL_RISK_SCHEMA = "capital-risk-v1"
_CAPITAL_RISK_SEED = 20261008
_MIN_PATHS = 100
_MAX_PATHS = 5000
_MAX_HORIZON = 1000
_MAX_PATH_STEPS = 1_000_000
_MAX_PNLS = 2000
_CURRENCY = re.compile(r"^[A-Z0-9]{2,12}$")


class SimulationCancelled(RuntimeError):
    """Raised when the owner cooperatively cancels a bounded simulation."""


@dataclass
class _PathBatch:
    finals: list[float]
    max_drawdowns: list[float]
    first_hits: list[int]
    fan_columns: list[array] | None


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必須是有限數字，bool 不算數字")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} 超出可計算範圍") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必須是有限數字")
    return number


def _bounded_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} 必須是整數，bool 不算整數")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} 必須介於 {minimum} 與 {maximum}")
    return value


def _r7(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("quantile values must be nonempty")
    h = (len(sorted_values) - 1) * probability
    lower = int(math.floor(h))
    upper = int(math.ceil(h))
    if lower == upper:
        return float(sorted_values[lower])
    fraction = h - lower
    try:
        value = math.fsum(
            (
                float(sorted_values[lower]) * (1.0 - fraction),
                float(sorted_values[upper]) * fraction,
            )
        )
    except (OverflowError, ValueError) as exc:
        raise ValueError("風險情境分位數發生溢位") from exc
    if not math.isfinite(value):
        raise ValueError("風險情境分位數不是有限值")
    return value


def _r7_summary(
    values: Sequence[float],
    *,
    check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if not values:
        raise ValueError("summary values must be nonempty")
    if check is not None:
        check()
    ordered = sorted(_finite_number(value, "情境結果") for value in values)
    try:
        mean_value = float(statistics.fmean(ordered))
    except (OverflowError, ValueError, statistics.StatisticsError) as exc:
        raise ValueError("風險情境平均值發生溢位") from exc
    if not math.isfinite(mean_value):
        raise ValueError("風險情境平均值不是有限值")
    if check is not None:
        check()
    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "p05": _r7(ordered, 0.05),
        "q1": _r7(ordered, 0.25),
        "median": _r7(ordered, 0.5),
        "q3": _r7(ordered, 0.75),
        "p95": _r7(ordered, 0.95),
        "maximum": ordered[-1],
        "mean": mean_value,
        "quantile_method": "linear-r7",
    }


def _edge(lower: float, upper: float, fraction: float) -> float:
    if fraction <= 0.0:
        return lower
    if fraction >= 1.0:
        return upper
    if lower < 0.0 < upper:
        value = math.fsum((lower * (1.0 - fraction), upper * fraction))
    else:
        value = lower + (upper - lower) * fraction
    if not math.isfinite(value):
        raise ValueError("風險情境直方圖邊界不是有限值")
    return value


def _terminal_histogram(
    values: Sequence[float],
    *,
    check: Callable[[], None] | None = None,
) -> list[dict[str, float | int]]:
    if check is not None:
        check()
    numbers = [_finite_number(value, "期末資金") for value in values]
    minimum, maximum = min(numbers), max(numbers)
    if minimum == maximum:
        return [{"lower": minimum, "upper": maximum, "count": len(numbers)}]
    bin_count = min(20, len(numbers))
    scale = max(abs(minimum), abs(maximum), 1.0)
    scaled_minimum = minimum / scale
    scaled_span = maximum / scale - scaled_minimum
    if not math.isfinite(scaled_span) or scaled_span <= 0.0:
        raise ValueError("風險情境直方圖範圍無效")
    counts = [0] * bin_count
    for number in numbers:
        if number >= maximum:
            index = bin_count - 1
        else:
            position = ((number / scale) - scaled_minimum) / scaled_span
            index = min(bin_count - 1, max(0, int(position * bin_count)))
        counts[index] += 1
    if check is not None:
        check()
    return [
        {
            "lower": _edge(minimum, maximum, index / bin_count),
            "upper": _edge(minimum, maximum, (index + 1) / bin_count),
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def _run_resampling_paths(
    pnls: Sequence[float],
    *,
    start_equity: float,
    threshold: float,
    horizon: int,
    paths: int,
    seed: int,
    comparison: str,
    check_initial: bool,
    collect_fan: bool,
    cancel_event: Any = None,
    progress_callback: Callable[[int, int], None] | None = None,
    deadline_seconds: float | None = None,
    absolute_deadline: float | None = None,
) -> _PathBatch:
    """One owned sampling engine used by both legacy and chart APIs."""

    if comparison not in {"lt", "le"}:
        raise ValueError("comparison must be lt or le")
    numbers = [_finite_number(value, "pnl") for value in pnls]
    if not numbers:
        raise ValueError("pnl 樣本不可為空")
    started = time.monotonic()
    deadline_at = (
        float(absolute_deadline)
        if absolute_deadline is not None
        else (
            started + float(deadline_seconds)
            if deadline_seconds is not None
            else None
        )
    )
    rng = random.Random(seed)
    finals: list[float] = []
    max_drawdowns: list[float] = []
    first_hits: list[int] = []
    columns = [array("d") for _ in range(horizon + 1)] if collect_fan else None
    progress_every = max(1, paths // 100)

    def hit(value: float) -> bool:
        return value < threshold if comparison == "lt" else value <= threshold

    def interrupted() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise SimulationCancelled("資金情境已取消")
        if deadline_at is not None and time.monotonic() >= deadline_at:
            raise TimeoutError("資金情境超過安全計算時間")

    if progress_callback is not None:
        progress_callback(0, paths)
    for path_index in range(paths):
        interrupted()
        equity = start_equity
        peak = equity
        max_drawdown = 0.0
        first_hit: int | None = 0 if check_initial and hit(equity) else None
        if columns is not None:
            columns[0].append(equity)

        for step in range(1, horizon + 1):
            if (step & 31) == 0:
                interrupted()
            if first_hit is None:
                sample = numbers[rng.randrange(len(numbers))]
                try:
                    equity += sample
                except OverflowError as exc:
                    raise ValueError("資金路徑加總發生溢位") from exc
                if not math.isfinite(equity):
                    raise ValueError("資金路徑加總不是有限值")
                peak = max(peak, equity)
                if peak > 0.0:
                    drawdown = (peak - equity) / peak
                else:
                    drawdown = 1.0
                if not math.isfinite(drawdown):
                    raise ValueError("資金路徑回撤不是有限值")
                max_drawdown = min(1.0, max(max_drawdown, drawdown))
                if hit(equity):
                    first_hit = step
            # Once hit, the actual crossing equity is held through the horizon.
            if columns is not None:
                columns[step].append(equity)

        finals.append(equity)
        max_drawdowns.append(max_drawdown)
        if first_hit is not None:
            first_hits.append(first_hit)
        completed = path_index + 1
        if progress_callback is not None and (
            completed == paths or completed % progress_every == 0
        ):
            progress_callback(completed, paths)
            # A callback can cancel or advance the clock at the terminal
            # progress notification. Re-check before returning the batch.
            interrupted()

    return _PathBatch(
        finals=finals,
        max_drawdowns=max_drawdowns,
        first_hits=first_hits,
        fan_columns=columns,
    )


def threshold_amount(
    start_equity: float, threshold_kind: str, threshold_value: float
) -> float:
    """Map one explicitly labelled threshold meaning to a capital amount."""

    equity = _finite_number(start_equity, "start_equity")
    if equity <= 0.0:
        raise ValueError("start_equity 必須大於 0")
    if not isinstance(threshold_kind, str):
        raise ValueError("threshold_kind 必須是文字")
    kind = threshold_kind.strip()
    value = _finite_number(threshold_value, "threshold_value")
    if kind in {"remaining_fraction", "loss_fraction"}:
        if not 0.0 <= value <= 1.0:
            raise ValueError("比例 threshold_value 必須介於 0 與 1")
        amount = equity * (value if kind == "remaining_fraction" else 1.0 - value)
    elif kind == "remaining_amount":
        if value < 0.0:
            raise ValueError("remaining_amount 不可小於 0")
        amount = value
    else:
        raise ValueError(
            "threshold_kind 必須是 remaining_fraction、remaining_amount 或 loss_fraction"
        )
    if not math.isfinite(amount):
        raise ValueError("資金門檻金額不是有限值")
    return amount


def simulate_capital_risk(
    pnls: Sequence[float],
    *,
    start_equity: float,
    currency: str,
    threshold_kind: str = "remaining_fraction",
    threshold_value: float = 0.10,
    future_trades: int = 200,
    paths: int = 1000,
    seed: int = _CAPITAL_RISK_SEED,
    cancel_event: Any = None,
    progress_callback: Callable[[int, int], None] | None = None,
    deadline_seconds: float = 15.0,
) -> dict[str, Any]:
    """Run a bounded fixed-currency PNL resampling scenario for charts.

    This is not compounding, position resizing, a market forecast, or broker
    liquidation. A path stops after it is *strictly* below the selected warning
    line and holds the actual crossing equity through the remaining horizon.
    """

    equity = _finite_number(start_equity, "start_equity")
    if equity <= 0.0:
        raise ValueError("start_equity 必須大於 0")
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency.strip().upper()):
        raise ValueError("currency 必須是已確認的結算幣別")
    normalized_currency = currency.strip().upper()
    horizon = _bounded_int(future_trades, "future_trades", 1, _MAX_HORIZON)
    path_count = _bounded_int(paths, "paths", _MIN_PATHS, _MAX_PATHS)
    if horizon * path_count > _MAX_PATH_STEPS:
        raise ValueError("future_trades × paths 不可超過 1,000,000")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed 必須是整數")
    deadline = _finite_number(deadline_seconds, "deadline_seconds")
    if deadline <= 0.0 or deadline > 60.0:
        raise ValueError("deadline_seconds 必須大於 0 且不超過 60")
    if not isinstance(pnls, Sequence) or isinstance(pnls, (str, bytes, bytearray)):
        raise ValueError("pnls 必須是數字序列")
    if len(pnls) < 10:
        raise ValueError("至少需要 10 筆完整的已平倉損益")
    if len(pnls) > _MAX_PNLS:
        raise ValueError(f"pnl 樣本不可超過 {_MAX_PNLS} 筆")
    numbers = [_finite_number(value, "pnl") for value in pnls]
    amount = threshold_amount(equity, threshold_kind, threshold_value)
    kind = threshold_kind.strip()
    value = _finite_number(threshold_value, "threshold_value")
    deadline_at = time.monotonic() + deadline

    def ensure_active() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise SimulationCancelled("資金情境已取消")
        if time.monotonic() >= deadline_at:
            raise TimeoutError("資金情境超過安全計算時間")

    batch = _run_resampling_paths(
        numbers,
        start_equity=equity,
        threshold=amount,
        horizon=horizon,
        paths=path_count,
        seed=seed,
        comparison="lt",
        check_initial=True,
        collect_fan=True,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        absolute_deadline=deadline_at,
    )
    ensure_active()
    assert batch.fan_columns is not None
    terminal = _r7_summary(batch.finals, check=ensure_active)
    ensure_active()
    fan: list[dict[str, float | int]] = []
    for step, column in enumerate(batch.fan_columns):
        ensure_active()
        summary = _r7_summary(column, check=ensure_active)
        ensure_active()
        fan.append(
            {
                "step": step,
                "p05": summary["p05"],
                "q1": summary["q1"],
                "median": summary["median"],
                "q3": summary["q3"],
                "p95": summary["p95"],
            }
        )
    # Do not retain the simulation matrix in the returned object.
    batch.fan_columns.clear()
    ensure_active()

    first_hit_stats = None
    if batch.first_hits:
        first_hit_stats = _r7_summary(
            [float(step) for step in batch.first_hits],
            check=ensure_active,
        )
        ensure_active()
    hit_count = len(batch.first_hits)
    warnings = [
        "這是情境頻率，不是未來發生機率或信賴保證。",
        "模型從過去固定幣別的單筆淨損益有放回抽樣；不複利、不調整部位，也不預測市場。",
        "各筆被假設為獨立同分布；連續交易、部位改變與市場制度轉換可能讓結果失真。",
        "樣本沒出現過的尾端大虧不會被抽到，有限路徑也可能漏掉罕見情境。",
        "跌破資金門檻後停止並保留實際穿越金額；這是警戒線假設，不是券商保證金強平規則。",
        "扇形帶是每一步的路徑分布，不是一條實際路徑，也不是未來信賴區間。",
    ]
    if len(numbers) < 30:
        warnings.insert(0, f"強烈警告：只有 {len(numbers)} 筆樣本，情境對單筆結果極度敏感。")
    zero_hit_note = (
        "本次有限模擬未出現跌破，不代表不可能。" if hit_count == 0 else None
    )
    ensure_active()
    terminal_histogram = _terminal_histogram(
        batch.finals,
        check=ensure_active,
    )
    ensure_active()
    result = {
        "schema_version": _CAPITAL_RISK_SCHEMA,
        "model": "fixed_currency_pnl_resampling_with_replacement",
        "start_equity": equity,
        "currency": normalized_currency,
        "threshold_kind": kind,
        "threshold_value": value,
        "threshold_amount": amount,
        "comparison": "strictly_below",
        "future_trades": horizon,
        "horizon": horizon,
        "paths": path_count,
        "seed": seed,
        "sample_count": len(numbers),
        "hit_count": hit_count,
        "hit_fraction": hit_count / path_count,
        "initially_below": equity < amount,
        "first_hit": first_hit_stats,
        "terminal": terminal,
        "terminal_histogram": terminal_histogram,
        "fan": fan,
        "stop_on_threshold": True,
        "end_below_equals_ever_below": True,
        "zero_hit_note": zero_hit_note,
        "warnings": warnings,
        "can_live": False,
    }
    ensure_active()
    return result


# Descriptive alias for callers that prefer the longer public name.
simulate_capital_risk_scenario = simulate_capital_risk


def gambler_ruin_probability(
    start: int, target: int, p_win: float
) -> float:
    """賭徒破產問題的**解析解**(用來驗證我們的模擬是否正確)。

    每一步 ±1,贏的機率 p,起始資本 a,在碰到 target 之前碰到 0 就破產。

        p ≠ 0.5:  r = q/p
                  P(破產) = (r^a − r^N) / (1 − r^N)
        p = 0.5:  P(破產) = 1 − a/N

    這是教科書結果(Feller)。我們用它當作模擬的正確性基準。
    """
    a, N = int(start), int(target)
    if a <= 0:
        return 1.0
    if a >= N:
        return 0.0
    if not (0.0 < p_win < 1.0):
        return 1.0 if p_win == 0.0 else 0.0
    if abs(p_win - 0.5) < 1e-12:
        return 1.0 - a / N
    r = (1.0 - p_win) / p_win
    return (r ** a - r ** N) / (1.0 - r ** N)


def losing_streak_probability(
    n_future_trades: int, streak: int, win_rate: float
) -> float:
    """以你的勝率,未來 N 筆交易裡出現「連續虧損 streak 次」的機率。

    用與倖存者偏差同一套精確 DP(把「贏」換成「輸」)。
    這是心理面最實用的數字:很多人不是被數學打敗,是被連虧打崩紀律。
    """
    loss_rate = 1.0 - win_rate
    return prob_streak_in_trials(n_future_trades, streak, loss_rate)


@dataclass
class RuinScenario:
    """未來情境的模擬結果。

    刻意命名為「情境」而非「預測」—— 這些數字回答的是
    「如果未來長得像過去,會怎樣」,而不是「未來會怎樣」。
    """

    n_future_trades: int
    n_paths: int
    start_equity: float
    ruin_threshold: float          # 權益跌破此值視為「爆掉」

    ruin_fraction: float           # 有多少比例的模擬路徑爆掉
    median_final_equity: float
    p05_final_equity: float
    p95_final_equity: float
    median_max_drawdown: float
    p95_max_drawdown: float
    median_trade_at_ruin: int | None   # 爆掉的路徑,中位數在第幾筆爆

    losing_streak_10_prob: float   # 未來出現連虧 10 次的機率

    warnings: list[str] = field(default_factory=list)
    # 起始權益是否為工具粗估(而非使用者提供)。爆倉比例對這個假設極度敏感:
    # 本金假設砍半,爆倉比例可能從 5% 跳到 90%。推估時必須醒目揭露。
    start_equity_inferred: bool = False

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


def simulate_ruin_scenario(
    pnls: list[float],
    *,
    start_equity: float | None = None,
    ruin_drawdown: float = 0.5,
    n_future_trades: int = 200,
    n_paths: int = 5000,
    seed: int = 20260710,
) -> RuinScenario | None:
    """用你實際的損益分布,bootstrap 模擬未來 N 筆交易的情境。

    做法:從你的 pnls 有放回地重抽,一筆一筆累積權益,
    看有多少比例的路徑會跌破「起始權益 × (1 − ruin_drawdown)」。

    Args:
        start_equity:  起始權益。未給時用「單筆最大虧損 × 20」的粗估。
        ruin_drawdown: 跌破起始權益的多少比例算「爆掉」(預設 50%)。
        n_paths:       模擬幾條路徑。

    Returns:
        RuinScenario;樣本 < 10 筆時回傳 None(誠實地不編數字)。
    """
    # 參數驗證:越界的參數會產生無意義的模擬,直接拒絕並講清楚,
    # 而不是默默算出垃圾數字。
    if n_paths < 1 or n_future_trades < 1:
        raise ValueError("n_paths 與 n_future_trades 必須 >= 1")
    if not (0.0 <= ruin_drawdown <= 1.0):
        raise ValueError(f"ruin_drawdown 必須在 [0, 1],收到 {ruin_drawdown}")
    # NaN 與 inf 都能通過「<= 0」檢查:inf 讓所有路徑第一筆就被判爆倉、
    # NaN 讓爆倉比例錯誤地變成 0 —— 必須用 isfinite 擋掉。
    if start_equity is not None and not (
        math.isfinite(start_equity) and start_equity > 0
    ):
        raise ValueError(f"start_equity 必須是 > 0 的有限數,收到 {start_equity}")

    # 資料污染先於樣本不足檢查:含 NaN/inf 時回「樣本不足」會掩蓋真正的問題
    if any(not math.isfinite(p) for p in pnls):
        raise ValueError("損益序列含 NaN/inf,無法模擬 —— 請先清理資料")
    n = len(pnls)
    if n < 10:
        return None

    equity_inferred = start_equity is None
    if start_equity is None:
        worst = abs(min(pnls)) if min(pnls) < 0 else abs(max(pnls))
        start_equity = max(worst * 20.0, 1.0)
    ruin_level = start_equity * (1.0 - ruin_drawdown)

    # Legacy API keeps its historical <= comparison and does not check k=0.
    # Sampling itself is shared with the chart-oriented strict-threshold API.
    batch = _run_resampling_paths(
        pnls,
        start_equity=start_equity,
        threshold=ruin_level,
        horizon=n_future_trades,
        paths=n_paths,
        seed=seed,
        comparison="le",
        check_initial=False,
        collect_fan=False,
    )
    finals = sorted(batch.finals)
    max_dds = sorted(batch.max_drawdowns)
    ruin_steps = sorted(batch.first_hits)
    ruined = len(ruin_steps)

    def q(sorted_vals: list[float], pct: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = min(len(sorted_vals) - 1, max(0, int(pct * len(sorted_vals))))
        return sorted_vals[idx]

    # 連虧機率要用「真實的虧損率」:打平交易(pnl == 0)既不是贏也不是虧,
    # 用 1 − win_rate 會把打平算成虧損,高估連虧機率。
    losses = sum(1 for p in pnls if p < 0)
    streak10 = losing_streak_probability(n_future_trades, 10, 1.0 - losses / n)

    warnings = []
    if equity_inferred:
        # 放最前面:爆倉比例對本金假設極度敏感,推估的本金必須第一眼看到。
        warnings.append(
            f"⚠️ 起始權益 {start_equity:,.0f} 是工具用『單筆最大虧損 × 20』**粗估的假設本金**,"
            "不是你的真實帳戶。爆倉比例對這個假設極度敏感 —— 請用 --equity 提供"
            "真實權益重跑,結果可能天差地遠。"
        )
    warnings += [
        "這**不是預測**。它假設「未來每一筆交易的損益,都從你過去的損益裡隨機抽出」——"
        "而未來必然不會如此。市場會變。",
        f"⚠️ 尾端低估:重抽只能抽到你樣本裡**出現過**的損益。你只有 {n} 筆樣本,"
        "如果那段期間剛好沒發生過大虧(例如選擇權賣方沒遇到暴跌),"
        "模擬就永遠抽不到它 —— 真實的爆倉機率會遠比這裡高。",
        "各筆交易被假設為獨立同分布。真實交易有序列相關(連續加碼、情緒化報復性交易),"
        "會讓實際的連虧與回撤比模擬更嚴重。",
        f"這些數字只有 {n} 筆樣本支撐。樣本一變,結果可能天差地遠 —— "
        "請把它當作「量級的感覺」,不要當作精確的機率。",
    ]

    return RuinScenario(
        n_future_trades=n_future_trades,
        n_paths=n_paths,
        start_equity=start_equity,
        ruin_threshold=ruin_level,
        ruin_fraction=ruined / n_paths,
        median_final_equity=q(finals, 0.5),
        p05_final_equity=q(finals, 0.05),
        p95_final_equity=q(finals, 0.95),
        median_max_drawdown=q(max_dds, 0.5),
        p95_max_drawdown=q(max_dds, 0.95),
        median_trade_at_ruin=(ruin_steps[len(ruin_steps) // 2] if ruin_steps else None),
        losing_streak_10_prob=streak10,
        warnings=warnings,
        start_equity_inferred=equity_inferred,
    )


def format_fraction(frac: float) -> str:
    """比例顯示紀律:開區間的值絕不能被捨入成端點。

    0.9996 若印成「100.0%」= 對讀者說「全爆」的假話(其實還有存活路徑);
    0.0004 若印成「0.0%」= 說「完全沒事」的假話。先格式化、再攔截端點字樣,
    改用「>99.9%」「<0.1%」誠實表達 —— 這個攔截法不依賴浮點捨入細節。
    終端報告與 HTML 報告都必須用這個格式器,不得各自 f-string。
    """
    if frac >= 1.0:
        return "100%"
    if frac <= 0.0:
        return "0%"
    # 整數百分比只用在 [1%, 99%] —— 這個區間的 :.0% 不可能捨入出 0%/100%,
    # 端點攔截因此只會命中真正貼近端點的值(複核輪抓到的教訓:
    # 0.005 若走 :.0% 會變 0% 再被誤標成 <0.1%,但真值是 0.5%)。
    text = f"{frac:.0%}" if 0.01 <= frac <= 0.99 else f"{frac:.1%}"
    if text in ("100%", "100.0%"):
        return ">99.9%"
    if text in ("0%", "0.0%"):
        return "<0.1%"
    return text


def render_scenario(s: RuinScenario) -> str:
    """輸出可讀報告。刻意用「情境」而非「預測」的措辭。"""
    L = ["=" * 66, "        風險情境模擬 — 如果未來長得像過去,會怎樣?", "=" * 66, ""]
    L.append(f"【設定】模擬未來 {s.n_future_trades} 筆交易,跑 {s.n_paths:,} 條路徑")
    inferred_tag = "(⚠️ 工具粗估,非真實帳戶)" if s.start_equity_inferred else ""
    L.append(f"       起始權益 {s.start_equity:,.0f}{inferred_tag},"
             f"跌破 {s.ruin_threshold:,.0f} 視為爆掉")
    L.append("")
    L.append("【情境結果】")

    # 爆倉比例:用「多少條路徑」而非「機率」措辭,並分級。
    # 格式化紀律見 format_fraction:開區間值絕不捨入成 0%/100%。
    frac = s.ruin_fraction
    pct = format_fraction(frac)
    if frac == 0:
        desc = "在這些情境裡,沒有一條路徑爆掉"
    elif frac < 0.01:
        desc = f"約每 100 條路徑不到 1 條爆掉({pct})"
    elif frac < 0.1:
        desc = f"約 {pct} 的路徑爆掉 —— 十次裡有一次以內"
    elif frac < 0.5:
        desc = f"⚠️ 約 {pct} 的路徑爆掉 —— 這是很高的比例"
    elif frac >= 1.0:
        desc = "🔴 全部的路徑都爆掉 —— 這套玩法在這些情境裡沒有活路"
    else:
        desc = f"🔴 超過一半({pct})的路徑爆掉 —— 這套玩法極可能毀掉你"
    L.append(f"  爆倉情境    : {desc}")
    if s.median_trade_at_ruin:
        L.append(f"  爆掉的路徑中,中位數在第 {s.median_trade_at_ruin} 筆交易時爆")

    L.append(f"  最終權益    : 中位數 {s.median_final_equity:,.0f}"
             f"(5%~95% 區間 {s.p05_final_equity:,.0f} ~ {s.p95_final_equity:,.0f})")
    L.append("            (爆掉的路徑在爆倉當下就停止 —— 現實中你會被強制平倉,")
    L.append("             不會用負的資金繼續交易)")
    L.append(f"  最大回撤    : 中位數 {format_fraction(s.median_max_drawdown)},"
             f"最壞 5% 的情境達 {format_fraction(s.p95_max_drawdown)}")
    L.append(f"  連虧 10 次  : 在未來 {s.n_future_trades} 筆裡出現的機率 "
             f"{format_fraction(s.losing_streak_10_prob)}")
    L.append("            (以你目前的勝率計算。連虧不是「會不會」,是「什麼時候」——")
    L.append("             問題是那時候你還守得住紀律嗎?)")
    L.append("")
    L.append("【你必須知道的限制】")
    for w in s.warnings:
        L.append(f"  ⚠ {w}")
    L.append("")
    L.append("─" * 66)
    L.append("這是「情境」不是「預測」。我們不提供 Kelly 部位建議,也不給 VaR 數字 ——")
    L.append("在幾十筆樣本下,那些量抖動劇烈,給出來就是假精準。")
    return "\n".join(L)
