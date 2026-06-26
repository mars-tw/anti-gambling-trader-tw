"""紙上模擬券商(Paper Trading)。

這是一個「完整可用」的券商實作 —— 不碰真錢,但會真的模擬撮合、
計算持倉與損益。交易者可以用它把整套交易程式跑到滿意為止,
再考慮是否要換成真實券商。

這是設計上的核心安全策略:預設值就是安全的。
"""

from __future__ import annotations

from .base import (
    AccountInfo,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderType,
    Position,
)


class PaperBroker(BrokerAdapter):
    """以記憶體模擬的紙上券商。

    Args:
        cash:        起始資金
        fee_rate:    每筆成交的手續費率(模擬交易成本)
        slippage:    市價單的模擬滑價率
        price_feed:  可選的報價函式 symbol -> price;未提供時需用 set_price 餵價
    """

    name = "paper"
    is_live = False

    def __init__(
        self,
        cash: float = 1_000_000.0,
        *,
        fee_rate: float = 0.001,
        slippage: float = 0.0005,
        currency: str = "USD",
        price_feed=None,
    ) -> None:
        super().__init__()
        self._cash = cash
        self._fee_rate = fee_rate
        self._slippage = slippage
        self._currency = currency
        self._price_feed = price_feed
        self._prices: dict[str, float] = {}
        self._positions: dict[str, Position] = {}
        self._order_seq = 0
        self.fills: list[OrderResult] = []   # 成交紀錄,方便事後分析

    # ── 報價 ──────────────────────────────────────────────
    def set_price(self, symbol: str, price: float) -> None:
        """手動餵入最新報價(回測 / 模擬時逐根餵價)。"""
        self._prices[symbol] = price
        if symbol in self._positions:
            self._positions[symbol].market_price = price

    def get_price(self, symbol: str) -> float:
        if self._price_feed is not None:
            price = self._price_feed(symbol)
            self.set_price(symbol, price)
            return price
        if symbol not in self._prices:
            raise ValueError(
                f"PaperBroker 沒有 {symbol} 的報價。請先 set_price() 或提供 price_feed。"
            )
        return self._prices[symbol]

    # ── 帳戶 / 持倉 ───────────────────────────────────────
    def connect(self) -> None:
        # 紙上模擬無需連線
        pass

    def get_account(self) -> AccountInfo:
        equity = self._cash + sum(
            p.quantity * self._prices.get(p.symbol, p.avg_price)
            for p in self._positions.values()
        )
        return AccountInfo(cash=self._cash, equity=equity, currency=self._currency)

    def get_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.quantity != 0]

    # ── 下單(模擬撮合)────────────────────────────────────
    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()   # PaperBroker is_live=False,永遠放行;保留以示範正確用法
        order.validate()

        ref_price = self.get_price(order.symbol)
        # 市價單套用滑價,限價單以限價成交
        if order.order_type == OrderType.MARKET:
            slip = self._slippage if order.side == OrderSide.BUY else -self._slippage
            fill_price = ref_price * (1 + slip)
        else:
            fill_price = order.limit_price or ref_price

        notional = fill_price * order.quantity
        fee = abs(notional) * self._fee_rate

        signed_qty = order.quantity if order.side == OrderSide.BUY else -order.quantity
        cost = notional + fee if order.side == OrderSide.BUY else -notional + fee

        if order.side == OrderSide.BUY and cost > self._cash:
            return OrderResult(
                ok=False, message=f"資金不足:需 {cost:,.2f},現金 {self._cash:,.2f}"
            )

        self._cash -= cost
        self._apply_fill(order.symbol, signed_qty, fill_price)

        self._order_seq += 1
        result = OrderResult(
            ok=True,
            order_id=f"PAPER-{self._order_seq:06d}",
            filled_quantity=order.quantity,
            avg_price=fill_price,
            message="paper fill",
            raw={"fee": fee, "tag": order.client_tag},
        )
        self.fills.append(result)
        return result

    def _apply_fill(self, symbol: str, signed_qty: float, price: float) -> None:
        """更新持倉的加權平均成本。"""
        pos = self._positions.get(symbol)
        if pos is None or pos.quantity == 0:
            self._positions[symbol] = Position(symbol, signed_qty, price, price)
            return
        new_qty = pos.quantity + signed_qty
        if new_qty == 0:
            pos.quantity = 0
            return
        # 同向加碼才更新均價;反向減碼維持原均價
        if (pos.quantity > 0) == (signed_qty > 0):
            total_cost = pos.avg_price * pos.quantity + price * signed_qty
            pos.avg_price = total_cost / new_qty
        pos.quantity = new_qty
        pos.market_price = price

    def cancel_order(self, order_id: str) -> bool:
        # 紙上模擬為立即成交,無掛單可取消
        return False
