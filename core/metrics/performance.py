"""核心績效指標。

這裡計算的不只是「賺多少」,更重要的是「這份獲利的品質與穩定度」。
單看勝率會騙人(高勝率可能搭配巨大虧損),單看總獲利也會騙人
(可能來自一兩筆幸運的暴賺)。所以我們同時看多個互補的角度。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..models import TradeLog


@dataclass
class PerformanceMetrics:
    """一份交易紀錄的完整績效畫像。"""

    # ── 基本計數 ────────────────────────────────
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0

    # ── 勝率與盈虧比 ─────────────────────────────
    win_rate: float = 0.0              # 勝率 = 獲利筆數 / 總筆數
    avg_win: float = 0.0              # 平均每筆獲利金額
    avg_loss: float = 0.0            # 平均每筆虧損金額(取正值)
    payoff_ratio: float = 0.0        # 盈虧比 = 平均獲利 / 平均虧損
    profit_factor: float = 0.0       # 獲利因子 = 總獲利 / 總虧損

    # ── 期望值(最關鍵的單一數字)────────────────
    expectancy: float = 0.0          # 每筆交易的期望損益(金額)
    expectancy_r: float = 0.0        # 以 R 為單位的期望值(平均 R-multiple)

    # ── 總體報酬 ────────────────────────────────
    total_pnl: float = 0.0
    total_fees: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0

    # ── 風險指標 ────────────────────────────────
    max_drawdown: float = 0.0        # 最大回撤(金額)
    max_drawdown_pct: float = 0.0    # 最大回撤(相對峰值百分比)
    max_consecutive_losses: int = 0  # 最長連續虧損次數
    sharpe: float = 0.0              # 夏普值(以每筆交易報酬計,非年化)
    sortino: float = 0.0            # 索提諾值(只懲罰下行波動)

    # ── R-multiple 分布(用於判斷是否「靠少數暴賺撐場」)──
    r_multiples: list[float] = field(default_factory=list)
    largest_win: float = 0.0
    largest_loss: float = 0.0
    top_trade_pnl_share: float = 0.0  # 最賺那一筆佔總獲利的比例

    # ── 交易風格 ────────────────────────────────
    avg_holding_days: float = 0.0
    is_mostly_intraday: bool = False  # 是否多為當沖/極短線(影響賭博判斷)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        # r_multiples 通常很長,摘要即可
        d["r_multiples_count"] = len(self.r_multiples)
        d.pop("r_multiples", None)
        return d


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def compute_metrics(log: TradeLog) -> PerformanceMetrics:
    """從交易紀錄計算完整績效指標。"""
    m = PerformanceMetrics()
    trades = list(log.sorted_by_time())
    m.total_trades = len(trades)
    if not trades:
        return m

    pnls = [t.pnl or 0.0 for t in trades]
    returns = [t.return_pct for t in trades]

    win_pnls = [p for p in pnls if p > 0]
    loss_pnls = [p for p in pnls if p < 0]

    m.wins = len(win_pnls)
    m.losses = len(loss_pnls)
    m.breakeven = m.total_trades - m.wins - m.losses

    m.win_rate = _safe_div(m.wins, m.total_trades)
    m.gross_profit = sum(win_pnls)
    m.gross_loss = abs(sum(loss_pnls))
    m.total_pnl = sum(pnls)
    m.total_fees = sum(t.fees for t in trades)

    m.avg_win = _safe_div(m.gross_profit, m.wins)
    m.avg_loss = _safe_div(m.gross_loss, m.losses)
    m.payoff_ratio = _safe_div(m.avg_win, m.avg_loss)
    m.profit_factor = _safe_div(m.gross_profit, m.gross_loss)

    # 期望值:每筆交易平均能賺/賠多少
    # E = 勝率 × 平均獲利 − 敗率 × 平均虧損
    loss_rate = _safe_div(m.losses, m.total_trades)
    m.expectancy = m.win_rate * m.avg_win - loss_rate * m.avg_loss

    # ── R-multiple:把每筆損益用「該筆的風險」標準化 ──
    # 以「平均虧損」作為 1R 的代理(沒有明確停損時的常見近似)。
    one_r = m.avg_loss if m.avg_loss > 0 else (abs(min(pnls)) if pnls else 1.0)
    one_r = one_r or 1.0
    m.r_multiples = [p / one_r for p in pnls]
    m.expectancy_r = _safe_div(sum(m.r_multiples), len(m.r_multiples))

    m.largest_win = max(pnls) if pnls else 0.0
    m.largest_loss = min(pnls) if pnls else 0.0
    # 最賺一筆佔總獲利比例:過高代表獲利集中在運氣,非穩定優勢
    m.top_trade_pnl_share = _safe_div(max(pnls, default=0.0), m.gross_profit)

    # ── 回撤:依時間順序累積權益,計算離峰值的最大跌幅 ──
    #
    # 回撤金額單純是「權益離峰值的最大跌幅」。但回撤『百分比』需要一個
    # 合理的基準資本 —— 若用「從零起算的累積損益峰值」當分母,峰值可能
    # 極小甚至接近 0,百分比會爆衝成上萬 %,毫無意義。
    #
    # 我們改用「估計初始資本」當分母:取所有交易中『最大單筆投入金額』
    # 作為帳戶資本規模的下限代理(實務上帳戶至少要撐得起最大那筆部位)。
    # 這讓回撤百分比落在「相對於帳戶規模」的合理區間。
    position_sizes = [abs(t.entry_price * t.quantity) for t in trades]
    capital_base = max(position_sizes, default=0.0)

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    m.max_drawdown = max_dd
    # 以初始資本為基準;資本基準加上已實現峰值,反映「帳戶真正能動用的高水位」
    denom = capital_base + peak
    m.max_drawdown_pct = _safe_div(max_dd, denom) if denom > 0 else 0.0

    # ── 最長連續虧損 ──
    streak = 0
    longest = 0
    for p in pnls:
        if p < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    m.max_consecutive_losses = longest

    # ── 夏普 / 索提諾(以每筆交易報酬率計算,非年化)──
    # 注意:這是「每筆交易」口徑,非年化。不同交易頻率的策略不可直接互比,
    # 僅用於同一份紀錄內的相對風險衡量。
    n = len(returns)
    mean_ret = _safe_div(sum(returns), n)
    if n > 1:
        var = sum((r - mean_ret) ** 2 for r in returns) / (n - 1)
        std = math.sqrt(var)
        m.sharpe = _safe_div(mean_ret, std)
        # 下行偏差:標準定義的分母是「全部樣本數」,不是只有下行筆數。
        # 用 len(downside) 當分母會系統性高估下行偏差、低估 Sortino,
        # 且方向錯誤(好策略下行少、分母小,反而被壓低)。以 0 為門檻,
        # 對每筆取 min(r, 0)^2,分母用 n-1 與夏普一致。
        dvar = sum(min(r, 0.0) ** 2 for r in returns) / (n - 1)
        dstd = math.sqrt(dvar)
        m.sortino = _safe_div(mean_ret, dstd)

    # ── 交易風格 ──
    holding = [t.holding_days for t in trades]
    m.avg_holding_days = _safe_div(sum(holding), len(holding))
    intraday_count = sum(1 for h in holding if h < 1.0)
    m.is_mostly_intraday = _safe_div(intraday_count, len(holding)) > 0.7

    return m
