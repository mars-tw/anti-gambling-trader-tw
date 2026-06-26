"""更多市場的券商 / 交易所範例框架。

加密貨幣:OKX、Bybit,以及 ccxt(一個介面接 100+ 交易所)。
美股:補上以 ccxt 風格之外的常見選擇。

同樣是「只寫到定位」的骨架,交易者填入自己的 API key 與實作,自負風險。
真實下單前務必先用各平台的測試網 / 紙上交易確認無誤。
"""

from __future__ import annotations

from .registry import BrokerTemplate

_IMPORT = '''from broker_lib import (
    AccountInfo, BrokerAdapter, Order, OrderResult, OrderSide, OrderType, Position,
)
'''


# ── ccxt:一個介面接 100+ 加密貨幣交易所 ────────────────────
_CCXT = BrokerTemplate(
    key="ccxt",
    name="ccxt(統一接 100+ 加密貨幣交易所)",
    market="crypto",
    sdk_install="pip install ccxt",
    notes=(
        "ccxt 用同一套介面接幾乎所有主流交易所(Binance/OKX/Bybit/Kraken…)。"
        "把 exchange_id 換成你的交易所即可。建立 API key 時請『關閉提領權限』,"
        "並先在交易所的測試網確認。"
    ),
    code=f'''"""ccxt 通用加密貨幣交易所連接器(範例框架 — 請填入你的實作)。

把 exchange_id 換成你的交易所(如 "binance"、"okx"、"bybit"、"kraken"…),
ccxt 會用同一套介面處理,換交易所幾乎不用改程式。
"""

{_IMPORT}

class CcxtBroker(BrokerAdapter):
    name = "ccxt"
    is_live = True   # 真實下單 —— 受安全閘門保護

    def __init__(self, exchange_id: str, api_key: str, api_secret: str,
                 password: str = "", sandbox: bool = True):
        super().__init__()
        self.exchange_id = exchange_id      # 如 "okx"、"bybit"、"binance"
        self.api_key = api_key
        self.api_secret = api_secret
        self.password = password            # 部分交易所(如 OKX)需要 passphrase
        self.sandbox = sandbox              # 強烈建議先用測試網
        self.exchange = None

    def connect(self) -> None:
        import ccxt
        cls = getattr(ccxt, self.exchange_id)
        params = {{"apiKey": self.api_key, "secret": self.api_secret}}
        if self.password:
            params["password"] = self.password
        self.exchange = cls(params)
        if self.sandbox and self.exchange.has.get("sandbox"):
            self.exchange.set_sandbox_mode(True)

    def get_account(self) -> AccountInfo:
        bal = self.exchange.fetch_balance()
        usdt = float(bal.get("USDT", {{}}).get("free", 0) or 0)
        return AccountInfo(cash=usdt, equity=usdt, currency="USDT")

    def get_positions(self) -> list[Position]:
        # 現貨用餘額表示;合約可用 fetch_positions()
        out = []
        bal = self.exchange.fetch_balance()
        for asset, info in bal.get("total", {{}}).items():
            if info and float(info) > 0 and asset != "USDT":
                price = self.get_price(f"{{asset}}/USDT") if asset != "USDT" else 1.0
                out.append(Position(f"{{asset}}/USDT", float(info), price))
        return out

    def get_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()   # 真實下單前的安全檢查,務必保留
        order.validate()
        side = "buy" if order.side == OrderSide.BUY else "sell"
        otype = "market" if order.order_type == OrderType.MARKET else "limit"
        try:
            resp = self.exchange.create_order(
                order.symbol, otype, side, order.quantity, order.limit_price
            )
            return OrderResult(
                ok=True, order_id=str(resp.get("id", "")),
                filled_quantity=float(resp.get("filled", 0) or 0),
                avg_price=float(resp.get("average") or order.limit_price or 0),
                raw=resp,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(ok=False, message=str(exc))

    def cancel_order(self, order_id: str) -> bool:
        # ccxt 取消通常需要 symbol;請依你的交易所補上
        # self.exchange.cancel_order(order_id, symbol)
        raise NotImplementedError
''',
)


# ── OKX(專屬範本,示範非 Binance 的另一大交易所)───────────
_OKX = BrokerTemplate(
    key="okx",
    name="OKX(加密貨幣)",
    market="crypto",
    sdk_install="pip install ccxt  # 建議用 ccxt 接 OKX,介面統一",
    notes=(
        "OKX 的 API 需要 api_key + secret + passphrase 三項。"
        "建立 key 時關閉提領權限,並先用 demo trading 測試。"
        "最省事的方式是用上面的 ccxt 範本,把 exchange_id 設為 'okx'。"
    ),
    code=f'''"""OKX 連接器(範例框架)。建議直接用 ccxt 範本並設 exchange_id='okx'。

若要用 OKX 官方 SDK(python-okx),請依其文件實作以下方法。
"""

{_IMPORT}

class OKXBroker(BrokerAdapter):
    name = "okx"
    is_live = True

    def __init__(self, api_key: str, api_secret: str, passphrase: str,
                 sandbox: bool = True):
        super().__init__()
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase        # OKX 特有
        self.sandbox = sandbox

    def connect(self) -> None:
        # TODO: 用 python-okx 或 ccxt 初始化。ccxt 範例:
        #   import ccxt
        #   self.ex = ccxt.okx({{"apiKey": self.api_key, "secret": self.api_secret,
        #                        "password": self.passphrase}})
        #   if self.sandbox: self.ex.set_sandbox_mode(True)
        raise NotImplementedError("請依 OKX 官方 SDK 或 ccxt 實作 connect()")

    def get_account(self) -> AccountInfo:
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        return []

    def get_price(self, symbol: str) -> float:
        raise NotImplementedError

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError
''',
)


# ── Bybit(專屬範本)────────────────────────────────────────
_BYBIT = BrokerTemplate(
    key="bybit",
    name="Bybit(加密貨幣)",
    market="crypto",
    sdk_install="pip install pybit  # 或用 ccxt 接 Bybit",
    notes=(
        "Bybit 官方 SDK 為 pybit。建立 key 時關閉提領,先用 testnet 測試。"
        "也可用 ccxt 範本把 exchange_id 設為 'bybit'。"
    ),
    code=f'''"""Bybit 連接器(範例框架)。可用官方 pybit 或 ccxt。"""

{_IMPORT}

class BybitBroker(BrokerAdapter):
    name = "bybit"
    is_live = True

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        super().__init__()
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.session = None

    def connect(self) -> None:
        # TODO: from pybit.unified_trading import HTTP
        #   self.session = HTTP(testnet=self.testnet, api_key=self.api_key,
        #                       api_secret=self.api_secret)
        raise NotImplementedError("請依 Bybit pybit 官方文件實作 connect()")

    def get_account(self) -> AccountInfo:
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        return []

    def get_price(self, symbol: str) -> float:
        raise NotImplementedError

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError
''',
)


# ── 美股:Tradier(REST、對開發者友善)──────────────────────
_TRADIER = BrokerTemplate(
    key="tradier",
    name="Tradier(美股,REST API)",
    market="us_stock",
    sdk_install="pip install requests  # Tradier 為純 REST,用 requests 即可",
    notes=(
        "Tradier 提供 sandbox 環境與 REST API,對開發者友善。"
        "請先用 sandbox token 測試,確認後再換正式 token。"
    ),
    code=f'''"""Tradier 美股連接器(範例框架 — REST API)。"""

{_IMPORT}

class TradierBroker(BrokerAdapter):
    name = "tradier"
    is_live = True

    def __init__(self, access_token: str, account_id: str, sandbox: bool = True):
        super().__init__()
        self.access_token = access_token
        self.account_id = account_id
        self.base = ("https://sandbox.tradier.com/v1" if sandbox
                     else "https://api.tradier.com/v1")

    def _headers(self):
        return {{"Authorization": f"Bearer {{self.access_token}}",
                 "Accept": "application/json"}}

    def connect(self) -> None:
        # REST API 無需持久連線;可在此驗證 token
        pass

    def get_account(self) -> AccountInfo:
        import requests
        r = requests.get(f"{{self.base}}/accounts/{{self.account_id}}/balances",
                         headers=self._headers())
        data = r.json().get("balances", {{}})
        cash = float(data.get("total_cash", 0) or 0)
        equity = float(data.get("total_equity", cash) or cash)
        return AccountInfo(cash=cash, equity=equity, currency="USD")

    def get_positions(self) -> list[Position]:
        # TODO: GET /accounts/{{id}}/positions,逐筆轉成 Position
        return []

    def get_price(self, symbol: str) -> float:
        import requests
        r = requests.get(f"{{self.base}}/markets/quotes",
                         params={{"symbols": symbol}}, headers=self._headers())
        q = r.json().get("quotes", {{}}).get("quote", {{}})
        return float(q.get("last", 0) or 0)

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        import requests
        side = "buy" if order.side == OrderSide.BUY else "sell"
        otype = "market" if order.order_type == OrderType.MARKET else "limit"
        payload = {{"class": "equity", "symbol": order.symbol, "side": side,
                    "quantity": int(order.quantity), "type": otype,
                    "duration": "day"}}
        if order.limit_price:
            payload["price"] = order.limit_price
        r = requests.post(f"{{self.base}}/accounts/{{self.account_id}}/orders",
                          data=payload, headers=self._headers())
        resp = r.json().get("order", {{}})
        ok = str(resp.get("status", "")).lower() in ("ok", "pending", "open")
        return OrderResult(ok=ok, order_id=str(resp.get("id", "")), raw=resp)

    def cancel_order(self, order_id: str) -> bool:
        import requests
        requests.delete(
            f"{{self.base}}/accounts/{{self.account_id}}/orders/{{order_id}}",
            headers=self._headers())
        return True
''',
)


MORE_BROKER_TEMPLATES = [_CCXT, _OKX, _BYBIT, _TRADIER]
