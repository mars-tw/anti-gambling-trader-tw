"""統計檢定工具 — 不依賴 scipy,純標準函式庫實作。

我們要回答的核心問題是:
「這個策略的『平均每筆賺錢』,有沒有可能其實只是運氣?」

用兩種互補的方法(兩者的 p 值語意相同:**假設你其實沒有優勢(H0)時,
純靠抽樣波動出現至少這麼極端結果的機率** —— 不是「優勢為真的機率」):
1. t 檢定:常態近似下的單尾尾端機率
2. Bootstrap 重抽樣:把樣本平移到 H0(均值 0)後重抽,看「重抽平均 ≥ 觀察值」
   的頻率(shift method;不假設分布,對偏態厚尾的損益特別重要)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# 單尾 α=0.05 的常態分位數,與 80% 檢定力的分位數。
# 樣本量估計需同時涵蓋「型 I 錯誤」與「型 II 錯誤(檢定力)」——
# 只用 z_alpha(或原本寫死的常數 2)等於只有約 50% 檢定力,會低估所需樣本。
Z_ALPHA_ONE_SIDED = 1.6449
Z_POWER_80 = 0.8416

# 負期望時「所需樣本量」沒有意義的哨兵值(對外一律轉成 None,不顯示給使用者)
NEGATIVE_EDGE_SENTINEL = 9999

# bootstrap 總抽樣次數上限(n × n_bootstrap)。純 Python 抽樣約 300 萬次/秒,
# 10,000 筆 × 5,000 次 = 5,000 萬次 ≈ 17 秒 —— 整個 analyze 管線會跑到 ~24 秒。
# 超過上限時自動調降重抽次數(但不低於 1,000 次,分位數索引仍有 25 的精度)。
# 誠實揭露:大樣本下 t 檢定本就極可靠,bootstrap 是第二道保險,降次數不損結論。
MAX_BOOTSTRAP_DRAWS = 20_000_000


def _finite_numeric_sequence(values, *, label: str) -> list[float]:
    """Validate public numerical inputs before any short-sample early return."""

    checked: list[float] = []
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{label} 必須是有限數字序列") from exc
    for index, value in enumerate(iterator):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError(
                f"{label}[{index}] 必須是有限數字（bool 不算金融數值）"
            )
        checked.append(float(value))
    return checked


def _finite_sum(values: list[float], *, label: str) -> float:
    try:
        total = math.fsum(values)
    except OverflowError as exc:
        raise ValueError(f"{label} 計算溢位") from exc
    if not math.isfinite(total):
        raise ValueError(f"{label} 計算結果不是有限數字")
    return total


def _finite_sample_variance(values: list[float], mean: float, *, label: str) -> float:
    squares: list[float] = []
    for value in values:
        delta = value - mean
        squared = delta * delta
        if not math.isfinite(delta) or not math.isfinite(squared):
            raise ValueError(f"{label} 變異數計算溢位")
        squares.append(squared)
    variance = _finite_sum(squares, label=f"{label} 變異數") / (len(values) - 1)
    if not math.isfinite(variance):
        raise ValueError(f"{label} 變異數計算結果不是有限數字")
    return variance


@dataclass
class SignificanceResult:
    """期望值顯著性檢定的結果。"""

    n: int                       # 樣本數
    mean: float                  # 樣本平均
    std: float                   # 樣本標準差
    t_stat: float                # t 統計量
    p_value_t: float             # t 檢定的單尾 p 值(H0: mean <= 0)
    p_value_bootstrap: float     # H0(期望=0)置中重抽下,平均值 >= 觀察值的比例
                                 # (單尾 p 值;不是「期望不為正的機率」)
    ci_low: float                # 平均值的 95% 信賴區間下界(bootstrap)
    ci_high: float               # 上界
    is_significant: bool         # 在 α=0.05 下是否顯著為正


def _normal_cdf(x: float) -> float:
    """標準常態累積分布函數(用 erf)。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _betacf(a: float, b: float, x: float, *, max_iter: int = 200,
            eps: float = 1e-12) -> float:
    """不完全 beta 函數的連分數展開(Lentz 演算法)。"""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _reg_incomplete_beta(x: float, a: float, b: float) -> float:
    """正則化不完全 beta 函數 I_x(a, b),純標準庫實作。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(ln_beta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _student_t_sf(t: float, df: int) -> float:
    """Student-t 分布的單尾存活函數 P(T > t),用真正的 t 分布 CDF。

    透過正則化不完全 beta 函數計算(不依賴 scipy)。這取代了先前自創的
    收縮近似 —— 對小樣本(df 5~15)的尾端機率才會正確,而非拍腦袋的常數。
    """
    if df <= 0:
        return 1.0
    if t == 0:
        return 0.5
    x = df / (df + t * t)
    # I_x(df/2, 1/2) 給的是雙尾機率;單尾依 t 的正負對半分配
    ib = _reg_incomplete_beta(x, df / 2.0, 0.5)
    if t > 0:
        return 0.5 * ib
    return 1.0 - 0.5 * ib


def test_expectancy_positive(
    pnls: list[float],
    *,
    n_bootstrap: int = 5000,
    alpha: float = 0.05,
    seed: int = 1234,
) -> SignificanceResult:
    """檢定「每筆交易的平均損益是否顯著大於 0」。

    這是分辨「真優勢」與「賭博」最關鍵的一步:
    一個賭徒即使長期期望值為負,短期也可能因運氣而帳面為正。
    我們要問的是 — 在統計上,我們有多大把握說這個正期望值不是運氣?

    Args:
        pnls:         每筆交易的損益(已扣成本)
        n_bootstrap:  bootstrap 重抽次數
        alpha:        顯著水準(預設 0.05)
        seed:         亂數種子,確保結果可重現

    Returns:
        SignificanceResult
    """
    if (
        isinstance(alpha, bool)
        or not isinstance(alpha, (int, float))
        or not math.isfinite(float(alpha))
        or not 0 < alpha < 1
    ):
        raise ValueError(f"alpha 必須介於 0 與 1 之間,收到 {alpha}")

    # n_bootstrap < 1 會導致除以零(p_boot)或空 list 索引(CI 分位數),
    # 且 CLI 的 --bootstrap 直通這裡 —— 必須在入口擋下,給清楚的錯誤訊息。
    if (
        isinstance(n_bootstrap, bool)
        or not isinstance(n_bootstrap, int)
        or n_bootstrap < 1
    ):
        raise ValueError(f"n_bootstrap 必須是正整數,收到 {n_bootstrap}")

    pnls = _finite_numeric_sequence(pnls, label="pnls")

    n = len(pnls)
    if n == 0:
        return SignificanceResult(0, 0, 0, 0, 1.0, 1.0, 0, 0, False)

    mean = _finite_sum(pnls, label="pnl 總和") / n
    if not math.isfinite(mean):
        raise ValueError("pnl 平均值不是有限數字")
    if n < 2:
        # 單筆樣本無法做任何統計推論 — 一律視為不顯著
        return SignificanceResult(n, mean, 0.0, 0.0, 1.0, 1.0, mean, mean, False)

    # 大樣本自動調降重抽次數(見 MAX_BOOTSTRAP_DRAWS 的說明)
    if n * n_bootstrap > MAX_BOOTSTRAP_DRAWS:
        n_bootstrap = max(1000, MAX_BOOTSTRAP_DRAWS // n)

    var = _finite_sample_variance(pnls, mean, label="pnls")
    std = math.sqrt(var)

    # ── t 檢定 ──
    se = std / math.sqrt(n) if std > 0 else 0.0
    if se > 0:
        t_stat = mean / se
        if not math.isfinite(t_stat):
            raise ValueError("t 統計量計算溢位")
        p_t = _student_t_sf(t_stat, n - 1)
    else:
        # 標準差為 0:所有交易損益相同。全正則確定獲利,全負則確定虧損
        t_stat = math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
        p_t = 0.0 if mean > 0 else 1.0

    # ── Bootstrap ──
    # 用 random.choices 一次抽整批(CPython C 實作):實測比逐一 randrange
    # 快約 4.6 倍。分布完全相同(均勻、有放回),同 seed 仍可重現。
    #
    # p 值語意(第 9 輪外部審查修正):假設檢定的 p 值必須在「虛無假設
    # 成立的世界」裡重抽 —— 把樣本平移成均值 0(shift method,教科書標準),
    # 再問「純靠抽樣波動,平均值至少跟觀察值一樣高的機率」。
    # 舊版直接對原始樣本重抽、數「平均 <= 0 的比例」:那是信賴區間的
    # 反推(percentile CI inversion),對偏態的損益分布會偏,
    # 而且曾被解釋成「期望其實不為正的機率」—— 那是後驗機率的語氣,
    # 頻率學派的 p 值不能那樣講。CI 本身維持 percentile 法(語意正確)。
    rng = random.Random(seed)
    shifted = [p - mean for p in pnls]  # 虛無假設:真實期望 = 0
    if not all(math.isfinite(value) for value in shifted):
        raise ValueError("置中 bootstrap 計算溢位")
    boot_means: list[float] = []        # 供 CI:重抽均值 = H0 重抽均值 + mean
    n_ge_obs = 0
    for _ in range(n_bootstrap):
        bm0 = _finite_sum(
            rng.choices(shifted, k=n), label="bootstrap 重抽總和"
        ) / n   # H0 世界的平均
        if bm0 >= mean:
            n_ge_obs += 1
        boot_mean = bm0 + mean
        if not math.isfinite(boot_mean):
            raise ValueError("bootstrap 平均值計算溢位")
        boot_means.append(boot_mean)
    boot_means.sort()
    # (n+1)/(B+1) 修正:蒙地卡羅 p 值不該印出「恰好 0」的假精準 ——
    # 觀察值本身也算一次「至少一樣極端」的實現
    p_boot = (n_ge_obs + 1) / (n_bootstrap + 1)

    lo_idx = int((alpha / 2) * n_bootstrap)
    hi_idx = min(int((1 - alpha / 2) * n_bootstrap), n_bootstrap - 1)
    ci_low = boot_means[lo_idx]
    ci_high = boot_means[hi_idx]

    # 同時要求兩種檢定都過關,才算顯著(雙重保險,偏保守)
    is_sig = (p_t < alpha) and (p_boot < alpha) and (mean > 0)

    return SignificanceResult(
        n=n,
        mean=mean,
        std=std,
        t_stat=t_stat,
        p_value_t=p_t,
        p_value_bootstrap=p_boot,
        ci_low=ci_low,
        ci_high=ci_high,
        is_significant=is_sig,
    )


def required_sample_size(win_rate: float, payoff_ratio: float) -> int:
    """粗估「要多少筆交易,才足以驗證這個策略不是運氣」。

    直覺:勝率越接近 50%、盈虧比越接近 1,訊號越微弱,
    需要越多樣本才能從雜訊中分辨出真實優勢。

    這是一個經驗性的指引值,不是嚴格的統計檢定力分析,
    目的是讓使用者對「我交易的次數夠不夠」有量化的概念。
    """
    # 無虧損樣本的 payoff_ratio 是 inf(不適用):二項模型算不了
    # (inf-inf=NaN),回預設門檻,由呼叫端的樣本量判斷去把關
    if win_rate <= 0 or win_rate >= 1 or payoff_ratio <= 0 or math.isinf(payoff_ratio):
        return 100

    # 每筆的期望(R 為單位)與其變異,用來估所需樣本
    edge = win_rate * payoff_ratio - (1 - win_rate)
    if edge <= 0:
        return NEGATIVE_EDGE_SENTINEL  # 負期望:再多樣本也驗證不出「優勢」

    # 報酬的近似變異(白努利 × 報酬幅度)
    var = (
        win_rate * (payoff_ratio - edge) ** 2
        + (1 - win_rate) * (-1 - edge) ** 2
    )
    sd = math.sqrt(var)
    # n ≈ ((z_alpha + z_power) * sd / edge)^2
    # 原本用常數 2,約等於只有 50% 檢定力(等於擲硬幣決定能不能驗出優勢),
    # 系統性低估所需樣本、給使用者「我交易夠多了」的錯誤安心。
    n = ((Z_ALPHA_ONE_SIDED + Z_POWER_80) * sd / edge) ** 2
    return max(30, int(math.ceil(n)))


def required_sample_size_from_pnls(
    pnls: list[float], *, alpha: float = 0.05, power: float = 0.8
) -> int | None:
    """用『真實的損益樣本變異』估算所需樣本量(比二項模型貼近現實)。

    n ≈ ((z_alpha + z_power) * std / mean)^2

    二項模型假設「贏必得 payoff 個 R、輸必失 1 個 R」(組內零變異),
    但真實交易的贏家之間、輸家之間離散度很大,因此二項模型只是
    **最樂觀的下限**。有實際 pnl 時應優先用這個函式。

    Returns:
        所需樣本數;若平均損益 <= 0(負期望)則回傳 None(再多樣本也沒用)。
    """
    if (
        isinstance(alpha, bool)
        or not isinstance(alpha, (int, float))
        or not math.isfinite(float(alpha))
        or not 0 < alpha < 1
    ):
        raise ValueError(f"alpha 必須介於 0 與 1 之間,收到 {alpha}")
    if (
        isinstance(power, bool)
        or not isinstance(power, (int, float))
        or not math.isfinite(float(power))
        or not 0 < power < 1
    ):
        raise ValueError(f"power 必須介於 0 與 1 之間,收到 {power}")

    pnls = _finite_numeric_sequence(pnls, label="pnls")
    n = len(pnls)
    if n < 2:
        return None
    mean = _finite_sum(pnls, label="pnl 總和") / n
    if mean <= 0:
        return None
    var = _finite_sample_variance(pnls, mean, label="pnls")
    std = math.sqrt(var)
    if std == 0:
        return 30
    # alpha 是公開參數,不可只收進簽名卻仍永遠用 0.05 的常數。
    # 單尾檢定的臨界值為 z_(1-alpha);檢定力則用 z_power。
    z_alpha = (
        Z_ALPHA_ONE_SIDED
        if abs(alpha - 0.05) <= 1e-12 else _z_from_power(1 - alpha)
    )
    z_power = (
        Z_POWER_80
        if abs(power - 0.8) <= 1e-12 else _z_from_power(power)
    )
    z = z_alpha + z_power
    need = (z * std / mean) ** 2
    if not math.isfinite(need):
        raise ValueError("所需樣本數計算溢位")
    return max(30, int(math.ceil(need)))


def _z_from_power(power: float) -> float:
    """由檢定力反推 z(標準常態分位數),用二分法,純標準庫。"""
    lo, hi = -6.0, 6.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _normal_cdf(mid) < power:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass
class TwoSampleResult:
    """兩獨立樣本『平均值是否不同』的 Welch 檢定結果(雙尾)。

    專用於「早期 vs 近期」這種**單一、事先指定**的比較。
    刻意不提供「掃描多個切點找最像衰退的那個」的介面 —— 那是資料探勘,
    會把雜訊當成訊號(見 trend 模組與 strategy/per_tag 的多重比較說明)。
    """

    n1: int
    n2: int
    mean1: float
    mean2: float
    diff: float                  # mean1 - mean2
    t_stat: float
    df: float                    # Welch–Satterthwaite 自由度
    p_value: float               # 雙尾 p 值(H0: 兩者平均相等)
    is_significant: bool         # 在給定 alpha 下是否顯著不同


def welch_mean_test(
    a: list[float], b: list[float], *, alpha: float = 0.05
) -> TwoSampleResult | None:
    """Welch 兩樣本 t 檢定(不假設等變異),雙尾檢定兩組平均是否不同。

    回傳 None 表示無法檢定(任一組樣本 < 2,或兩組變異都為 0)。

    設計為**雙尾**而非單尾:因為使用者的問題是「我在進步還是退步?」——
    方向未知,不該預設只找「退步」。方向由呼叫端依 diff 的正負描述,
    但顯著與否只做一次對稱的檢定,避免『兩個方向各測一次』變相 p-hacking。
    """
    if (
        isinstance(alpha, bool)
        or not isinstance(alpha, (int, float))
        or not math.isfinite(float(alpha))
        or not 0 < alpha < 1
    ):
        raise ValueError(f"alpha 必須介於 0 與 1 之間,收到 {alpha}")
    a = _finite_numeric_sequence(a, label="a")
    b = _finite_numeric_sequence(b, label="b")
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1 = _finite_sum(a, label="a 總和") / n1
    m2 = _finite_sum(b, label="b 總和") / n2
    v1 = _finite_sample_variance(a, m1, label="a")
    v2 = _finite_sample_variance(b, m2, label="b")
    se2 = v1 / n1 + v2 / n2
    if not math.isfinite(se2):
        raise ValueError("Welch 標準誤計算溢位")
    if se2 <= 0:
        # 兩組內部都零變異:平均相同→不顯著;不同→視為確定不同
        diff = m1 - m2
        return TwoSampleResult(
            n1, n2, m1, m2, diff,
            t_stat=0.0 if diff == 0 else math.copysign(math.inf, diff),
            df=float(n1 + n2 - 2),
            p_value=1.0 if diff == 0 else 0.0,
            is_significant=diff != 0,
        )
    se = math.sqrt(se2)
    t = (m1 - m2) / se
    if not math.isfinite(t):
        raise ValueError("Welch t 統計量計算溢位")
    # Welch–Satterthwaite 自由度
    df = se2 ** 2 / (
        (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    )
    if not math.isfinite(df):
        raise ValueError("Welch 自由度計算溢位")
    # 雙尾 p:單尾存活函數對稱處理
    p = 2.0 * _student_t_sf(abs(t), df)
    p = min(1.0, max(0.0, p))
    return TwoSampleResult(
        n1=n1, n2=n2, mean1=m1, mean2=m2, diff=m1 - m2,
        t_stat=t, df=df, p_value=p, is_significant=p < alpha,
    )
