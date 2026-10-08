"""風險情境模擬 — 回答「我這套玩下去會不會爆倉?」

⚠️ 這是本專案最危險的模組,因為它產生「未來」的數字,很容易被誤讀成預言。
   所以我們刻意做了這些限制:

   1. **不叫「破產機率」,叫「情境估計」。** 這些數字建立在
      「未來的損益分布和過去一樣」這個必然為假的假設上。

   2. **不畫窄信賴區間。** 模擬誤差(重抽次數帶來的)很小,
      但**估計誤差**(你只有 30 筆樣本)極大。實測:同一份 22 筆的資料,
      破產率的真實不確定性橫跨 [0.005, 1.000]。在一個橫跨 0~100% 的量
      旁邊掛個 ±1% 的區間,是製造虛假的精確感。

   3. **不做 Kelly 部位建議、不做 VaR/CVaR 標題數字。**
      這些量在 n=20~35 時抖動劇烈,給出來就是假精準。

   4. **明確警告尾端低估。** bootstrap 只能重抽「你樣本裡出現過的損益」。
      如果你是選擇權賣方,而樣本期間剛好沒發生過爆倉,
      模擬**永遠抽不到爆倉** —— 破產機率會被系統性低估到接近 0。
      這正是最危險的情況。
"""

from .simulate import (
    RuinScenario,
    SimulationCancelled,
    format_fraction,
    gambler_ruin_probability,
    losing_streak_probability,
    render_scenario,
    simulate_capital_risk,
    simulate_capital_risk_scenario,
    simulate_ruin_scenario,
    threshold_amount,
)

__all__ = [
    "RuinScenario",
    "SimulationCancelled",
    "format_fraction",
    "gambler_ruin_probability",
    "losing_streak_probability",
    "simulate_capital_risk",
    "simulate_capital_risk_scenario",
    "simulate_ruin_scenario",
    "threshold_amount",
    "render_scenario",
]
