"""自動交易策略「骨架」產生器。

重要立場:本工具只產生「可回測的策略骨架」,絕不產生會連接真實
帳戶下單的程式碼。輸出的程式碼:
  - 預設只在『回測 / 紙上模擬』模式運行
  - 把所有下單函式留成需要使用者「自行實作且自行負責」的空殼
  - 在檔頭嵌入本工具的裁決結論;若裁決為勸退,程式碼預設拒絕啟用

這麼設計是因為:把賭博行為自動化,只會讓人賠得更快。
策略要先通過統計與回測驗證,才值得固化成程式。
"""

from __future__ import annotations

from .profiler import StrategyProfile
from ..verdict.judge import Verdict, VerdictLevel


def _verdict_banner(verdict: Verdict) -> str:
    """產生嵌入程式碼頂端的裁決橫幅(Python 註解形式)。"""
    lines = [
        "# " + "=" * 72,
        "# 反詐投資王 — 自動產生的策略骨架",
        "# " + "-" * 72,
        f"# 裁決等級: {verdict.level.value}",
        f"# 結論: {verdict.headline}",
        f"# 是否建議勸退: {'是' if verdict.should_discourage else '否'}",
        "# " + "-" * 72,
        "# 理由:",
    ]
    for r in verdict.reasons:
        lines.append(f"#   - {r}")
    if verdict.red_flags:
        lines.append("# 警訊:")
        for rf in verdict.red_flags:
            lines.append(f"#   [{rf.severity}] {rf.message}")
    lines += [
        "# " + "-" * 72,
        "# 免責聲明:",
        "#   本程式碼僅供回測與研究。它不會、也不應被用來自動下真實訂單。",
        "#   過去績效不代表未來表現。任何投資決策與後果由使用者自行承擔。",
        "# " + "=" * 72,
    ]
    return "\n".join(lines)


def _entry_logic_hint(profile: StrategyProfile) -> str:
    """依風格給出進場邏輯的程式碼提示(留待使用者具體化)。"""
    code = profile.style_code
    if code in ("scalp_intraday", "swing_short"):
        return (
            "        # 你的交易偏短線。常見可回測的進場訊號範例:\n"
            "        #   - 突破前 N 根高點 / 跌破前 N 根低點\n"
            "        #   - 短均線上穿長均線(如 5 日上穿 20 日)\n"
            "        #   - RSI 從超賣區回升\n"
            "        # 請把『你實際在看的訊號』寫成明確、可量化的條件:\n"
            "        signal = False  # TODO: 用你的進場規則取代\n"
        )
    if code in ("swing_medium", "position"):
        return (
            "        # 你的交易偏中期波段。常見可回測的進場訊號範例:\n"
            "        #   - 站上季線(60 日均線)且成交量放大\n"
            "        #   - 回測支撐不破後的反彈\n"
            "        signal = False  # TODO: 用你的進場規則取代\n"
        )
    return (
        "        # 你的交易偏長期投資。對長期投資而言,『定期定額 + 分散』\n"
        "        # 通常勝過擇時。若仍要擇時,請把規則明確量化:\n"
        "        signal = False  # TODO: 用你的進場規則取代\n"
    )


def generate_skeleton(
    profile: StrategyProfile,
    verdict: Verdict,
    *,
    framework: str = "backtrader",
) -> str:
    """產生策略骨架原始碼字串。

    Args:
        profile:   策略輪廓
        verdict:   裁決結果(會嵌入檔頭,並決定是否預設禁用)
        framework: "backtrader" 或 "vectorbt" 或 "generic"

    Returns:
        可寫入 .py 檔的完整原始碼字串
    """
    banner = _verdict_banner(verdict)
    discourage = verdict.should_discourage
    entry_hint = _entry_logic_hint(profile)

    # 是否預設禁用:若裁決勸退,程式碼一啟動就會擋下,逼使用者正視
    guard = "True" if discourage else "False"

    side_default = "long" if profile.dominant_side.value == "long" else "short"

    if framework == "backtrader":
        body = _BACKTRADER_TEMPLATE.format(
            banner=banner,
            guard=guard,
            style=profile.style,
            side=side_default,
            holding=round(profile.avg_holding_days, 1),
            entry_hint=entry_hint,
            level=verdict.level.value,
        )
    elif framework == "vectorbt":
        body = _VECTORBT_TEMPLATE.format(
            banner=banner,
            guard=guard,
            style=profile.style,
            level=verdict.level.value,
        )
    else:
        body = _GENERIC_TEMPLATE.format(
            banner=banner,
            guard=guard,
            style=profile.style,
            entry_hint=entry_hint,
        )
    return body


_BACKTRADER_TEMPLATE = '''{banner}

"""
策略風格(反推自你的交易紀錄): {style}
主要方向: {side}
平均持倉天數: {holding}

使用前請先安裝: pip install backtrader
本檔僅供『回測』。要接真實券商前,請三思並自行承擔法律與資金風險。
"""

import backtrader as bt

# 由裁決結果決定的安全開關。
# 若本策略被判定為『應勸退』(賭博 / 疑似運氣 / 樣本不足 / 脆弱優勢),
# 這個旗標為 True,執行時會直接中止 —— 逼你先把策略驗證好,再談自動化。
_DISCOURAGED = {guard}
_VERDICT_LEVEL = "{level}"


def _safety_gate():
    if _DISCOURAGED:
        raise SystemExit(
            "\\n⛔ 安全閘門啟動:本策略的統計裁決為『{level}』,不建議自動化執行。\\n"
            "   把賭博自動化,只會賠得更快。請先讓策略通過統計與樣本外回測,\\n"
            "   確認具備穩定優勢後,再手動把這個旗標改為 False(風險自負)。\\n"
        )


class ReverseEngineeredStrategy(bt.Strategy):
    """從你的歷史交易反推而成的策略骨架。

    這只是『起點』。進出場條件目前是空的,你必須把自己腦中的規則
    寫成明確、可量化的條件 —— 寫不出明確規則,本身就是一個警訊:
    代表你的交易可能沒有可重複的邏輯,而是憑感覺(也就是賭)。
    """

    params = dict(
        stop_loss=0.05,      # 停損 5%(務必設定 —— 沒有停損是賭徒的標誌)
        take_profit=0.15,    # 停利 15%
    )

    def __init__(self):
        _safety_gate()
        self.order = None

    def next(self):
        if self.order:
            return

        if not self.position:
{entry_hint}
            if signal:
                self.order = self.buy()
        else:
            # 出場:停損 / 停利。請依你的實際規則調整。
            entry = self.position.price
            price = self.data.close[0]
            if price <= entry * (1 - self.p.stop_loss):
                self.order = self.close()   # 觸發停損
            elif price >= entry * (1 + self.p.take_profit):
                self.order = self.close()   # 觸發停利


def run_backtest(data_feed, cash=1_000_000, commission=0.001):
    """跑一次回測。data_feed 為 backtrader 的資料來源。"""
    cerebro = bt.Cerebro()
    cerebro.addstrategy(ReverseEngineeredStrategy)
    cerebro.adddata(data_feed)
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    print(f"起始資金: {{cash:,.0f}}")
    results = cerebro.run()
    print(f"結束資金: {{cerebro.broker.getvalue():,.0f}}")
    return results


if __name__ == "__main__":
    _safety_gate()
    print("這是策略骨架。請填入進出場規則,並用 run_backtest() 搭配歷史資料回測。")
'''


_VECTORBT_TEMPLATE = '''{banner}

"""
策略風格(反推自你的交易紀錄): {style}

使用前請先安裝: pip install vectorbt
本檔僅供『回測』。
"""

import vectorbt as vbt
import numpy as np

_DISCOURAGED = {guard}
_VERDICT_LEVEL = "{level}"


def _safety_gate():
    if _DISCOURAGED:
        raise SystemExit(
            "⛔ 安全閘門:本策略裁決為『{level}』,不建議自動化。請先驗證優勢。"
        )


def build_signals(close):
    """從價格序列產生進出場訊號。

    目前為空殼 —— 請把你的進場 / 出場規則寫成布林陣列。
    例如(均線交叉):
        fast = vbt.MA.run(close, 10).ma
        slow = vbt.MA.run(close, 30).ma
        entries = fast.vbt.crossed_above(slow)
        exits = fast.vbt.crossed_below(slow)
    """
    entries = np.full(close.shape, False)   # TODO: 你的進場規則
    exits = np.full(close.shape, False)     # TODO: 你的出場規則
    return entries, exits


def run_backtest(close, fees=0.001, init_cash=1_000_000):
    _safety_gate()
    entries, exits = build_signals(close)
    pf = vbt.Portfolio.from_signals(
        close, entries, exits, fees=fees, init_cash=init_cash
    )
    print(pf.stats())
    return pf


if __name__ == "__main__":
    _safety_gate()
    print("這是 vectorbt 策略骨架。請於 build_signals() 填入你的規則。")
'''


_GENERIC_TEMPLATE = '''{banner}

"""
策略風格(反推自你的交易紀錄): {style}

這是不綁定特定框架的通用骨架,描述進出場決策的純函式。
你可以把它接到任何回測引擎,但本工具不提供真實下單功能。
"""

_DISCOURAGED = {guard}


def safety_gate():
    if _DISCOURAGED:
        raise SystemExit("⛔ 安全閘門:本策略裁決為應勸退,不建議自動化。")


def should_enter(context) -> bool:
    """回傳是否該進場。context 由你的回測引擎提供當前市場狀態。"""
{entry_hint}
    return signal


def should_exit(context, entry_price: float, current_price: float,
                stop_loss=0.05, take_profit=0.15) -> bool:
    """回傳是否該出場(預設停損 5% / 停利 15%)。"""
    if current_price <= entry_price * (1 - stop_loss):
        return True
    if current_price >= entry_price * (1 + take_profit):
        return True
    return False  # TODO: 加入你自己的出場訊號


if __name__ == "__main__":
    safety_gate()
    print("通用策略骨架。請實作 should_enter() 的進場規則。")
'''
