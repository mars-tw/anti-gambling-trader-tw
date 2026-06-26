"""券商範例框架註冊表。

這裡的每個範本都是「只寫到定位」的程式碼骨架 —— 交易者複製後,
填入自己的 API key 與下單實作。本工具刻意不內建真實金鑰呼叫,
以免在交易者尚未準備好時誤觸真錢下單。

涵蓋:
  台股:永豐 Shioaji、元大 SPARK、富邦新一代、凱基 KGI SUPER PY、
        群益/統一/元富 期貨類
  美股:Interactive Brokers、Alpaca、Tradier
  加密貨幣:Binance、OKX、Bybit、ccxt(統一接 100+ 交易所)
所有範本都繼承本套件的 BrokerAdapter,因此可直接替換 PaperBroker。
(台股與其他市場的範本分別放在 registry_tw.py 與 registry_more.py。)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class BrokerTemplate:
    """一個券商範例框架。"""

    key: str
    name: str
    market: str          # 主要市場
    sdk_install: str     # 安裝指令
    notes: str           # 重點提醒(尤其是「建立唯讀/受限 key」之類的安全提醒)
    code: str            # 範例程式碼(可寫入 .py)

    @property
    def class_name(self) -> str:
        """從範例程式碼自動抓出 Broker 類別名(如 YuantaBroker)。

        這樣新增券商範本時,不必在別處(如 scaffold 範本)同步維護
        key→類別名 的對照表,避免漏改造成 KeyError。
        """
        m = re.search(r"class\s+(\w+)\s*\(\s*BrokerAdapter\s*\)", self.code)
        return m.group(1) if m else f"{self.key.title()}Broker"


# ── Binance ───────────────────────────────────────────────
_BINANCE = BrokerTemplate(
    key="binance",
    name="Binance(加密貨幣)",
    market="crypto",
    sdk_install="pip install python-binance",
    notes=(
        "在 Binance 後台建立 API key 時,先只開『讀取』權限做測試;"
        "確認程式無誤後再考慮開啟交易權限。永遠不要開提領權限。"
    ),
    code='''"""Binance 券商連接器(範例框架 — 請填入你的實作)。"""

from broker_lib import (
    AccountInfo, BrokerAdapter, Order, OrderResult, OrderSide, OrderType, Position,
)


class BinanceBroker(BrokerAdapter):
    name = "binance"
    is_live = True   # 真實下單 —— 受安全閘門保護

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        super().__init__()
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet   # 強烈建議先用測試網
        self.client = None

    def connect(self) -> None:
        from binance.client import Client
        self.client = Client(self.api_key, self.api_secret, testnet=self.testnet)
        # TODO: 視需要驗證連線,例如 self.client.ping()

    def get_account(self) -> AccountInfo:
        acct = self.client.get_account()
        # TODO: 把 acct 解析成 cash / equity
        usdt = next((b for b in acct["balances"] if b["asset"] == "USDT"), {"free": 0})
        cash = float(usdt["free"])
        return AccountInfo(cash=cash, equity=cash, currency="USDT")

    def get_positions(self) -> list[Position]:
        # 現貨沒有傳統「持倉」概念,可用餘額代表
        # TODO: 依你的需求把非零幣別餘額轉成 Position
        return []

    def get_price(self, symbol: str) -> float:
        t = self.client.get_symbol_ticker(symbol=symbol)
        return float(t["price"])

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()   # 真實下單前的安全檢查,務必保留
        order.validate()
        side = "BUY" if order.side == OrderSide.BUY else "SELL"
        try:
            if order.order_type == OrderType.MARKET:
                resp = self.client.create_order(
                    symbol=order.symbol, side=side, type="MARKET",
                    quantity=order.quantity,
                )
            else:
                resp = self.client.create_order(
                    symbol=order.symbol, side=side, type="LIMIT",
                    timeInForce="GTC", quantity=order.quantity,
                    price=str(order.limit_price),
                )
            return OrderResult(
                ok=True, order_id=str(resp.get("orderId", "")),
                filled_quantity=float(resp.get("executedQty", 0)),
                avg_price=order.limit_price or self.get_price(order.symbol),
                raw=resp,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(ok=False, message=str(exc))

    def cancel_order(self, order_id: str) -> bool:
        # TODO: self.client.cancel_order(symbol=..., orderId=order_id)
        raise NotImplementedError
''',
)


# ── Interactive Brokers ───────────────────────────────────
_IBKR = BrokerTemplate(
    key="ibkr",
    name="Interactive Brokers(美股 / 全球)",
    market="us_stock",
    sdk_install="pip install ib_insync",
    notes=(
        "需開啟 TWS 或 IB Gateway 的 API。測試階段請在 TWS 設定中"
        "啟用『Read-Only API』,確認無誤後再關閉以允許下單。"
    ),
    code='''"""Interactive Brokers 券商連接器(範例框架 — 請填入你的實作)。"""

from broker_lib import (
    AccountInfo, BrokerAdapter, Order, OrderResult, OrderSide, OrderType, Position,
)


class IBKRBroker(BrokerAdapter):
    name = "ibkr"
    is_live = True

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        super().__init__()
        self.host, self.port, self.client_id = host, port, client_id
        self.ib = None

    def connect(self) -> None:
        from ib_insync import IB
        self.ib = IB()
        self.ib.connect(self.host, self.port, clientId=self.client_id)

    def get_account(self) -> AccountInfo:
        vals = {v.tag: v.value for v in self.ib.accountValues() if v.currency in ("USD", "BASE")}
        cash = float(vals.get("CashBalance", 0) or 0)
        equity = float(vals.get("NetLiquidation", cash) or cash)
        return AccountInfo(cash=cash, equity=equity, currency="USD")

    def get_positions(self) -> list[Position]:
        out = []
        for p in self.ib.positions():
            out.append(Position(p.contract.symbol, p.position, p.avgCost))
        return out

    def get_price(self, symbol: str) -> float:
        from ib_insync import Stock
        contract = Stock(symbol, "SMART", "USD")
        ticker = self.ib.reqMktData(contract)
        self.ib.sleep(1)
        return float(ticker.marketPrice())

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        from ib_insync import Stock, MarketOrder, LimitOrder
        contract = Stock(order.symbol, "SMART", "USD")
        action = "BUY" if order.side == OrderSide.BUY else "SELL"
        if order.order_type == OrderType.MARKET:
            ib_order = MarketOrder(action, order.quantity)
        else:
            ib_order = LimitOrder(action, order.quantity, order.limit_price)
        trade = self.ib.placeOrder(contract, ib_order)
        self.ib.sleep(1)
        return OrderResult(
            ok=True, order_id=str(trade.order.orderId),
            filled_quantity=trade.orderStatus.filled,
            avg_price=trade.orderStatus.avgFillPrice or 0.0,
        )

    def cancel_order(self, order_id: str) -> bool:
        # TODO: 用 self.ib.cancelOrder(...)
        raise NotImplementedError
''',
)


# ── Alpaca ────────────────────────────────────────────────
_ALPACA = BrokerTemplate(
    key="alpaca",
    name="Alpaca(美股,API 友善)",
    market="us_stock",
    sdk_install="pip install alpaca-py",
    notes="Alpaca 提供 paper trading 端點,測試時把 paper=True 即可用模擬帳戶。",
    code='''"""Alpaca 券商連接器(範例框架 — 請填入你的實作)。"""

from broker_lib import (
    AccountInfo, BrokerAdapter, Order, OrderResult, OrderSide, OrderType, Position,
)


class AlpacaBroker(BrokerAdapter):
    name = "alpaca"
    is_live = True

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        super().__init__()
        self.api_key, self.api_secret, self.paper = api_key, api_secret, paper
        self.client = None

    def connect(self) -> None:
        from alpaca.trading.client import TradingClient
        self.client = TradingClient(self.api_key, self.api_secret, paper=self.paper)

    def get_account(self) -> AccountInfo:
        a = self.client.get_account()
        return AccountInfo(cash=float(a.cash), equity=float(a.equity), currency="USD")

    def get_positions(self) -> list[Position]:
        return [
            Position(p.symbol, float(p.qty), float(p.avg_entry_price), float(p.current_price))
            for p in self.client.get_all_positions()
        ]

    def get_price(self, symbol: str) -> float:
        # 報價建議改用 alpaca 的 data client;此處示意
        raise NotImplementedError("請用 alpaca.data 取得即時報價")

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide as AS, TimeInForce
        side = AS.BUY if order.side == OrderSide.BUY else AS.SELL
        if order.order_type == OrderType.MARKET:
            req = MarketOrderRequest(symbol=order.symbol, qty=order.quantity,
                                     side=side, time_in_force=TimeInForce.DAY)
        else:
            req = LimitOrderRequest(symbol=order.symbol, qty=order.quantity, side=side,
                                    time_in_force=TimeInForce.DAY, limit_price=order.limit_price)
        resp = self.client.submit_order(req)
        return OrderResult(ok=True, order_id=str(resp.id),
                           filled_quantity=float(resp.filled_qty or 0))

    def cancel_order(self, order_id: str) -> bool:
        self.client.cancel_order_by_id(order_id)
        return True
''',
)


# ── 永豐 Shioaji(台股)──────────────────────────────────
_SHIOAJI = BrokerTemplate(
    key="shioaji",
    name="永豐 Shioaji(台股 / 台期)",
    market="tw_stock",
    sdk_install="pip install shioaji",
    notes=(
        "需先在永豐金證券開戶並申請 API 憑證。測試時請用模擬模式 "
        "(simulation=True),確認無誤再切換正式環境。"
    ),
    code='''"""永豐 Shioaji 券商連接器(範例框架 — 請填入你的實作)。"""

from broker_lib import (
    AccountInfo, BrokerAdapter, Order, OrderResult, OrderSide, OrderType, Position,
)


class ShioajiBroker(BrokerAdapter):
    name = "shioaji"
    is_live = True

    def __init__(self, api_key: str, secret_key: str, simulation: bool = True):
        super().__init__()
        self.api_key, self.secret_key, self.simulation = api_key, secret_key, simulation
        self.api = None

    def connect(self) -> None:
        import shioaji as sj
        self.api = sj.Shioaji(simulation=self.simulation)
        self.api.login(api_key=self.api_key, secret_key=self.secret_key)
        # TODO: 載入憑證 self.api.activate_ca(...)(正式下單必需)

    def get_account(self) -> AccountInfo:
        # TODO: 用 self.api.account_balance() 取得餘額
        bal = self.api.account_balance()
        cash = float(getattr(bal, "acc_balance", 0) or 0)
        return AccountInfo(cash=cash, equity=cash, currency="TWD")

    def get_positions(self) -> list[Position]:
        out = []
        for p in self.api.list_positions(self.api.stock_account):
            out.append(Position(p.code, float(p.quantity), float(p.price)))
        return out

    def get_price(self, symbol: str) -> float:
        contract = self.api.Contracts.Stocks[symbol]
        snapshot = self.api.snapshots([contract])[0]
        return float(snapshot.close)

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        import shioaji as sj
        contract = self.api.Contracts.Stocks[order.symbol]
        action = sj.constant.Action.Buy if order.side == OrderSide.BUY else sj.constant.Action.Sell
        price_type = (sj.constant.StockPriceType.MKT if order.order_type == OrderType.MARKET
                      else sj.constant.StockPriceType.LMT)
        sj_order = self.api.Order(
            price=order.limit_price or 0, quantity=int(order.quantity),
            action=action, price_type=price_type,
            order_type=sj.constant.OrderType.ROD,
            account=self.api.stock_account,
        )
        trade = self.api.place_order(contract, sj_order)
        return OrderResult(ok=True, order_id=str(trade.order.id), raw={"trade": str(trade)})

    def cancel_order(self, order_id: str) -> bool:
        # TODO: self.api.cancel_order(...)
        raise NotImplementedError
''',
)


def _all_templates() -> list[BrokerTemplate]:
    """彙整所有券商範本(內建 + 台股 + 其他市場)。

    台股與其他市場的範本放在獨立模組,避免本檔過度膨脹;在此延遲匯入合併。
    """
    base = [_BINANCE, _IBKR, _ALPACA, _SHIOAJI]
    from .registry_tw import TW_BROKER_TEMPLATES
    from .registry_more import MORE_BROKER_TEMPLATES
    return base + TW_BROKER_TEMPLATES + MORE_BROKER_TEMPLATES


BROKER_TEMPLATES: dict[str, BrokerTemplate] = {
    t.key: t for t in _all_templates()
}


def list_brokers() -> list[BrokerTemplate]:
    """列出所有可用的券商範例框架。"""
    return list(BROKER_TEMPLATES.values())
