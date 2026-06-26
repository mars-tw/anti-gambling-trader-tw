"""三市場交易成本模型。

很多人「帳面上」覺得自己賺錢,是因為他們沒把手續費、交易稅、滑價算進去。
這是賭徒最常見的自我欺騙之一。本模組在使用者沒提供 fees 時,
用各市場的「真實成本」自動估算,讓盈虧回到誠實的數字。

注意:這些是常見的預設值,使用者應依自己的券商/交易所實際費率調整。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Market, Side


@dataclass
class CostModel:
    """單一市場的成本參數。

    Attributes:
        commission_rate: 手續費率(雙邊各收一次,以成交金額計)
        commission_min:  最低手續費(每筆)
        tax_rate:        交易稅率(通常只在賣出時收)
        slippage_rate:   預估滑價率(每邊),反映成交價與理想價的偏差
    """

    commission_rate: float
    commission_min: float
    tax_rate: float
    slippage_rate: float

    def estimate(self, price: float, quantity: float, *, is_sell: bool) -> float:
        """估算「單邊」的成本(進場一次、出場一次各算一次)。"""
        notional = abs(price * quantity)
        commission = max(notional * self.commission_rate, self.commission_min)
        tax = notional * self.tax_rate if is_sell else 0.0
        slippage = notional * self.slippage_rate
        return commission + tax + slippage


# 各市場的預設成本模型(2026 年常見值,僅供估算)。
DEFAULT_COST_MODELS: dict[Market, CostModel] = {
    # 台股:手續費 0.1425%(常見打折後約 0.06%),賣出證交稅 0.3%(當沖 0.15%)
    Market.TW_STOCK: CostModel(
        commission_rate=0.001425,
        commission_min=20.0,      # 多數券商最低 20 元
        tax_rate=0.003,
        slippage_rate=0.0005,
    ),
    # 美股:多數券商零佣金,但有 SEC/FINRA 規費與點差滑價
    Market.US_STOCK: CostModel(
        commission_rate=0.0,
        commission_min=0.0,
        tax_rate=0.0000278,       # SEC 規費等,僅賣出
        slippage_rate=0.0005,
    ),
    # 加密貨幣:交易所現貨手續費約 0.1%(雙邊),滑價在小幣上可能很大
    Market.CRYPTO: CostModel(
        commission_rate=0.001,
        commission_min=0.0,
        tax_rate=0.0,
        slippage_rate=0.0010,
    ),
    Market.UNKNOWN: CostModel(
        commission_rate=0.001,
        commission_min=0.0,
        tax_rate=0.0,
        slippage_rate=0.0005,
    ),
}


def estimate_round_trip_cost(
    market: Market,
    side: Side,
    entry_price: float,
    exit_price: float,
    quantity: float,
    model: CostModel | None = None,
) -> float:
    """估算一筆「完整來回」交易的總成本(進場 + 出場)。

    做多:買進(進場,不收稅)→ 賣出(出場,收稅)
    做空:賣出(進場,收稅)→ 買回(出場,不收稅)
    """
    m = model or DEFAULT_COST_MODELS.get(market, DEFAULT_COST_MODELS[Market.UNKNOWN])

    if side == Side.LONG:
        entry_cost = m.estimate(entry_price, quantity, is_sell=False)
        exit_cost = m.estimate(exit_price, quantity, is_sell=True)
    else:  # SHORT
        entry_cost = m.estimate(entry_price, quantity, is_sell=True)
        exit_cost = m.estimate(exit_price, quantity, is_sell=False)

    return entry_cost + exit_cost
