"""券商介面的共同定義。

交易者只要實作 BrokerAdapter 的抽象方法,就能把任何券商接進來;
策略程式只依賴這個介面,不必知道背後是 Binance、IBKR 還是富邦。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"   # 市價單
    LIMIT = "limit"     # 限價單


@dataclass
class Order:
    """一張要送出的委託單。"""

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None      # 限價單必填
    client_tag: Optional[str] = None         # 對應策略標籤,方便日後回頭分析

    def validate(self) -> None:
        if self.quantity <= 0:
            raise ValueError("委託數量必須大於 0")
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("限價單必須提供 limit_price")


@dataclass
class OrderResult:
    """券商回報的成交/委託結果。"""

    ok: bool
    order_id: str = ""
    filled_quantity: float = 0.0
    avg_price: float = 0.0
    message: str = ""
    raw: dict = field(default_factory=dict)   # 券商原始回應,保留以利除錯


@dataclass
class Position:
    """目前持有的部位。"""

    symbol: str
    quantity: float          # 正=多單,負=空單
    avg_price: float
    market_price: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        return (self.market_price - self.avg_price) * self.quantity


@dataclass
class AccountInfo:
    """帳戶概況。"""

    cash: float
    equity: float
    currency: str = "USD"


class BrokerAdapter(ABC):
    """所有券商連接器的共同介面。

    交易者要接自己的券商,只需繼承這個類別並實作下列方法。
    策略程式只會呼叫這些方法,因此換券商不必改策略。

    安全護欄:
        is_live 屬性標示「這是不是真實下單」。框架在送出真實訂單前,
        會檢查 confirm_live_trading() 是否被明確開啟 —— 預設關閉,
        避免交易者在還沒準備好時就誤觸真錢下單。
    """

    #: 人類可讀名稱
    name: str = "abstract"

    #: 是否為真實下單(PaperBroker 為 False)
    is_live: bool = False

    def __init__(self) -> None:
        self._live_confirmed = False

    # ── 安全閘門 ──────────────────────────────────────────
    def confirm_live_trading(self, *, i_understand_the_risk: bool = False) -> None:
        """明確開啟真實下單。預設關閉。

        交易者必須親手呼叫並傳入 i_understand_the_risk=True,
        才能解除真實下單的封鎖。這是刻意的摩擦,逼你停下來想清楚。
        """
        if not i_understand_the_risk:
            raise PermissionError(
                "要開啟真實下單,必須明確傳入 i_understand_the_risk=True。\n"
                "請先確認:策略已通過統計與樣本外驗證、已充分紙上模擬、"
                "已設好部位上限與停損,且你願意自負一切後果。"
            )
        self._live_confirmed = True

    def _guard_live(self) -> None:
        """送出真實訂單前的最後一道檢查。"""
        if self.is_live and not self._live_confirmed:
            raise PermissionError(
                f"[{self.name}] 真實下單已被安全閘門攔下。\n"
                "請先呼叫 broker.confirm_live_trading(i_understand_the_risk=True)。"
            )

    # ── 交易者需實作的抽象方法 ───────────────────────────
    @abstractmethod
    def connect(self) -> None:
        """建立連線 / 驗證金鑰。"""

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """查詢帳戶概況。"""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """查詢目前持倉。"""

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """查詢最新成交價。"""

    @abstractmethod
    def place_order(self, order: Order) -> OrderResult:
        """送出委託單。實作時務必在最前面呼叫 self._guard_live()。"""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """取消委託。"""
