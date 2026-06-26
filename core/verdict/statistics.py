"""統計檢定工具 — 不依賴 scipy,純標準函式庫實作。

我們要回答的核心問題是:
「這個策略的『平均每筆賺錢』,有沒有可能其實只是運氣?」

用兩種互補的方法:
1. t 檢定:在常態近似下,平均報酬顯著大於 0 的機率
2. Bootstrap 重抽樣:不假設分布,直接從資料重抽,看「平均值 ≤ 0」的頻率
   (這對交易資料特別重要,因為損益分布通常嚴重偏態、厚尾)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class SignificanceResult:
    """期望值顯著性檢定的結果。"""

    n: int                       # 樣本數
    mean: float                  # 樣本平均
    std: float                   # 樣本標準差
    t_stat: float                # t 統計量
    p_value_t: float             # t 檢定的單尾 p 值(H0: mean <= 0)
    p_value_bootstrap: float     # bootstrap 下平均值 <= 0 的比例
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
    n = len(pnls)
    if n == 0:
        return SignificanceResult(0, 0, 0, 0, 1.0, 1.0, 0, 0, False)

    mean = sum(pnls) / n
    if n < 2:
        # 單筆樣本無法做任何統計推論 — 一律視為不顯著
        return SignificanceResult(n, mean, 0.0, 0.0, 1.0, 1.0, mean, mean, False)

    var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    std = math.sqrt(var)

    # ── t 檢定 ──
    se = std / math.sqrt(n) if std > 0 else 0.0
    if se > 0:
        t_stat = mean / se
        p_t = _student_t_sf(t_stat, n - 1)
    else:
        # 標準差為 0:所有交易損益相同。全正則確定獲利,全負則確定虧損
        t_stat = math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
        p_t = 0.0 if mean > 0 else 1.0

    # ── Bootstrap ──
    rng = random.Random(seed)
    boot_means: list[float] = []
    for _ in range(n_bootstrap):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        boot_means.append(sum(sample) / n)
    boot_means.sort()

    # 平均值 <= 0 的比例 ≈ 「期望其實不為正」的經驗機率
    n_le_zero = sum(1 for bm in boot_means if bm <= 0)
    p_boot = n_le_zero / n_bootstrap

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
    if win_rate <= 0 or win_rate >= 1 or payoff_ratio <= 0:
        return 100

    # 每筆的期望(R 為單位)與其變異,用來估所需樣本
    edge = win_rate * payoff_ratio - (1 - win_rate)
    if edge <= 0:
        return 9999  # 負期望:再多樣本也驗證不出「優勢」

    # 報酬的近似變異(白努利 × 報酬幅度)
    var = (
        win_rate * (payoff_ratio - edge) ** 2
        + (1 - win_rate) * (-1 - edge) ** 2
    )
    # 要讓 t ≈ 2(約 95% 信心),需 n ≈ (2 * sd / edge)^2
    sd = math.sqrt(var)
    n = (2 * sd / edge) ** 2
    return max(30, int(math.ceil(n)))
