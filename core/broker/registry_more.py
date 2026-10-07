"""更多市場的券商 / 交易所範例框架。

加密貨幣:Pionex、OKX、Bybit,以及 ccxt(一個介面接 100+ 交易所)。
美股:補上以 ccxt 風格之外的常見選擇。

全部都是產碼範本；有些含可呼叫的 API 示意，有些只寫到定位。交易者仍須自行
填入連線方式與驗證規則並自負風險。
真實下單前務必先用各平台的測試網 / 紙上交易確認無誤。
"""

from __future__ import annotations

from .registry import BrokerTemplate

_IMPORT = '''from broker_lib import (
    AccountInfo, BrokerAdapter, Order, OrderResult, OrderSide, OrderType, Position,
)
'''


# ── Pionex 派網（官方現貨 REST API）───────────────────────
_PIONEX = BrokerTemplate(
    key="pionex",
    name="Pionex 派網(加密貨幣現貨 REST API)",
    market="crypto",
    sdk_install="pip install requests>=2.31",
    notes=(
        "依 Pionex 官方 Trade API 實作現貨 REST 範本；官方 OpenAPI 只列正式站 "
        "https://api.pionex.com，沒有列現貨 sandbox。請先建立只有讀取權限且綁定 IP "
        "白名單的 key，在 PaperBroker 完整驗證；未完成極小額驗證階段前不要開交易權限。"
        "Pionex 市價買單使用報價幣 amount，而通用 Order.quantity 代表標的數量，"
        "因此本範本會保守拒絕市價買單，避免單位誤用。"
    ),
    code='''"""Pionex 派網現貨 REST API 連接器範本。

官方文件：https://www.pionex.com/docs/api-docs/zh-hant
Base URL：https://api.pionex.com

安全邊界：
- 只實作現貨 Trade API，不含 Beta 合約、網格機器人、理財或轉帳 API。
- 官方 OpenAPI 只列 production，沒有現貨 sandbox；本類別永遠視為 live broker。
- 請先使用唯讀、IP 白名單 API key。不要在程式或 git 內保存金鑰。
- Pionex MARKET BUY 使用報價幣 amount；通用 Order.quantity 是標的數量，為避免
  單位錯置，本範本拒絕 MARKET BUY。請用紙上模擬或明確限價單驗證。
- 帳戶餘額沒有成本價；Position.avg_price 只能暫以市價占位，因此不可拿
  unrealized_pnl 當真實未實現損益。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import time
from collections import deque
from decimal import Decimal, InvalidOperation

''' + _IMPORT + '''


class PionexBroker(BrokerAdapter):
    name = "pionex"
    is_live = True

    BASE_URL = "https://api.pionex.com"
    OFFICIAL_DOCS = "https://www.pionex.com/docs/api-docs/zh-hant"

    def __init__(self, api_key: str, api_secret: str, *,
                 quote_currency: str = "USDT", timeout: float = 10.0):
        super().__init__()
        self.api_key = str(api_key or "").strip()
        self.api_secret = str(api_secret or "").strip()
        self.quote_currency = str(quote_currency or "USDT").strip().upper()
        self.timeout = float(timeout)
        self.session = None
        self._order_symbols: dict[str, str] = {}
        self.last_cancel_details: dict | None = None
        self._request_window = deque()
        self._rate_blocked_until = 0.0

        if not re.fullmatch(r"[A-Z0-9]+", self.quote_currency):
            raise ValueError("quote_currency 必須是單一幣別代碼，例如 USDT")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout 必須是有限且大於 0 的數字")

    @staticmethod
    def _query_text(params: dict) -> str:
        """依官方規格：key 排序，簽章文字中的 value 不做 URL encode。"""
        items = []
        for key in sorted(params):
            value = params[key]
            if value is None:
                continue
            if isinstance(value, bool):
                value = "true" if value else "false"
            items.append(f"{key}={value}")
        return "&".join(items)

    def _signature(self, method: str, path: str, query_text: str,
                   body_text: str = "") -> str:
        signing_text = f"{method.upper()}{path}?{query_text}"
        if method.upper() in {"POST", "DELETE"}:
            signing_text += body_text
        return hmac.new(
            self.api_secret.encode("utf-8"),
            signing_text.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _decimal_text(value, label: str) -> str:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{label} 不是有效數字") from exc
        if not number.is_finite() or number <= 0:
            raise ValueError(f"{label} 必須是有限且大於 0 的數字")
        return format(number, "f")

    @staticmethod
    def _api_decimal(value, label: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise RuntimeError(f"Pionex 回傳無效的 {label}") from exc
        if not number.is_finite() or number < 0:
            raise RuntimeError(f"Pionex 回傳無效的 {label}")
        return number

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        value = str(symbol or "").strip().upper().replace("/", "_").replace("-", "_")
        if not re.fullmatch(r"[A-Z0-9]+_[A-Z0-9]+", value):
            raise ValueError(
                "Pionex 現貨代號必須明示 base_quote，例如 BTC_USDT；"
                "不接受可能混淆的 BTCUSDT"
            )
        return value

    def _require_session(self):
        if self.session is None:
            raise RuntimeError("尚未 connect()；請先用唯讀 API key 驗證連線")
        return self.session

    def _rate_gate(self, weight: int, *, private: bool) -> None:
        """單一 adapter 內保守遵守官方每秒 10 weight 限制。

        多程序仍須由使用者在外層做全域節流；本範本不會對 429 自動重試。
        """
        if not isinstance(weight, int) or isinstance(weight, bool) or not 1 <= weight <= 9:
            raise ValueError("Pionex request weight 必須是 1 到 9 的整數")
        now = time.monotonic()
        if now < self._rate_blocked_until:
            remaining = int(self._rate_blocked_until - now) + 1
            raise RuntimeError(f"Pionex 速率限制冷卻中；至少再等 {remaining} 秒")
        while self._request_window and now - self._request_window[0][0] >= 1.0:
            self._request_window.popleft()
        ip_weight = sum(item[1] for item in self._request_window)
        account_weight = sum(item[1] for item in self._request_window if item[2])
        if ip_weight + weight > 9 or (private and account_weight + weight > 9):
            wait_for = max(0.01, 1.0 - (now - self._request_window[0][0]))
            time.sleep(wait_for)
            return self._rate_gate(weight, private=private)
        self._request_window.append((time.monotonic(), weight, private))

    def _decode_response(self, response) -> dict:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == 429:
            self._rate_blocked_until = time.monotonic() + 60.0
            raise RuntimeError("Pionex API 超過速率限制；請停止重試至少 60 秒")
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            if status_code < 200 or status_code >= 300:
                raise RuntimeError(
                    f"Pionex HTTP {status_code or '?'} 請求失敗且回傳非 JSON"
                ) from exc
            raise RuntimeError(
                f"Pionex HTTP {getattr(response, 'status_code', '?')} 回傳非 JSON"
            ) from exc
        code = str(payload.get("code", "UNKNOWN") or "UNKNOWN") if isinstance(payload, dict) else "UNKNOWN"
        message = str(payload.get("message", "request failed") or "request failed") if isinstance(payload, dict) else "request failed"
        if code == "INVALID_TIMESTAMP":
            message += "；請同步系統時間（官方容許範圍約 ±20 秒）"
        if status_code < 200 or status_code >= 300:
            raise RuntimeError(
                f"Pionex HTTP {status_code or '?'} / API {code}: {message}"
            )
        if not isinstance(payload, dict) or payload.get("result") is not True:
            raise RuntimeError(f"Pionex API {code}: {message}")
        return payload

    def _private_request(self, method: str, path: str, *,
                         params: dict | None = None,
                         body: dict | None = None,
                         weight: int = 1) -> dict:
        session = self._require_session()
        self._rate_gate(weight, private=True)
        query = dict(params or {})
        query.setdefault("timestamp", int(time.time() * 1000))
        query_text = self._query_text(query)
        body_text = "" if body is None else json.dumps(
            body, ensure_ascii=False, separators=(",", ":")
        )
        headers = {
            "PIONEX-KEY": self.api_key,
            "PIONEX-SIGNATURE": self._signature(method, path, query_text, body_text),
            "Accept": "application/json",
        }
        if method.upper() in {"POST", "DELETE"}:
            headers["Content-Type"] = "application/json"
        response = session.request(
            method.upper(),
            self.BASE_URL + path,
            params=[(key, query[key]) for key in sorted(query) if query[key] is not None],
            data=body_text if method.upper() in {"POST", "DELETE"} else None,
            headers=headers,
            timeout=self.timeout,
            allow_redirects=False,
        )
        return self._decode_response(response)

    def _public_get(self, path: str, params: dict | None = None, *,
                    weight: int = 1) -> dict:
        session = self._require_session()
        self._rate_gate(weight, private=False)
        response = session.get(
            self.BASE_URL + path,
            params=params or {},
            headers={"Accept": "application/json"},
            timeout=self.timeout,
            allow_redirects=False,
        )
        return self._decode_response(response)

    def connect(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ValueError("Pionex api_key / api_secret 不可空白；請勿把金鑰提交到 git")
        import requests
        self.session = requests.Session()
        # 唯讀查詢驗證金鑰；不會下單，也不會改變帳戶狀態。
        self._balance_rows()

    def _balance_rows(self) -> list[dict]:
        payload = self._private_request("GET", "/api/v1/account/balances")
        rows = payload.get("data", {}).get("balances")
        if not isinstance(rows, list):
            raise RuntimeError("Pionex balances 回應缺少 data.balances")
        return rows

    def _spot_prices(self) -> dict[str, Decimal]:
        payload = self._public_get("/api/v1/market/tickers", {"type": "SPOT"})
        rows = payload.get("data", {}).get("tickers")
        if not isinstance(rows, list):
            raise RuntimeError("Pionex tickers 回應缺少 data.tickers")
        prices = {}
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            if symbol and row.get("close") not in (None, ""):
                price = self._api_decimal(row["close"], f"{symbol} close")
                if price > 0:
                    prices[symbol] = price
        return prices

    def _symbol_info(self, pair: str) -> dict:
        payload = self._public_get(
            "/api/v1/common/symbols",
            {"symbols": pair, "type": "SPOT"},
            weight=5,
        )
        rows = payload.get("data", {}).get("symbols")
        if not isinstance(rows, list):
            raise RuntimeError("Pionex symbols 回應缺少 data.symbols")
        info = next(
            (row for row in rows if str(row.get("symbol", "")).upper() == pair),
            None,
        )
        if info is None:
            raise ValueError(f"Pionex 找不到現貨代號 {pair}")
        if str(info.get("type", "")).upper() != "SPOT" or info.get("enable") is not True:
            raise ValueError(f"Pionex 現貨代號 {pair} 目前不可交易")
        base, quote = pair.split("_", 1)
        if str(info.get("baseCurrency", "")).upper() != base:
            raise RuntimeError(f"Pionex {pair} 規格缺少正確 baseCurrency")
        if str(info.get("quoteCurrency", "")).upper() != quote:
            raise RuntimeError(f"Pionex {pair} 規格缺少正確 quoteCurrency")
        return info

    @staticmethod
    def _decimal_places(number: Decimal) -> int:
        exponent = number.normalize().as_tuple().exponent
        return max(0, -exponent)

    @classmethod
    def _optional_api_limit(cls, info: dict, key: str) -> Decimal | None:
        value = info.get(key)
        if value in (None, ""):
            return None
        number = cls._api_decimal(value, key)
        return number if number > 0 else None

    @classmethod
    def _required_api_limit(cls, info: dict, key: str) -> Decimal:
        number = cls._optional_api_limit(info, key)
        if number is None:
            raise RuntimeError(f"Pionex symbol 規格缺少有效 {key}，拒絕送單")
        return number

    @staticmethod
    def _api_precision(info: dict, key: str) -> int:
        try:
            number = Decimal(str(info[key]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError(f"Pionex symbol 規格缺少有效 {key}") from exc
        if not number.is_finite() or number != number.to_integral_value():
            raise RuntimeError(f"Pionex symbol 規格缺少有效 {key}")
        precision = int(number)
        if not 0 <= precision <= 18:
            raise RuntimeError(f"Pionex symbol 規格的 {key} 超出安全範圍")
        return precision

    def _validate_order_rules(self, info: dict, *, quantity: Decimal,
                              price: Decimal | None, order_type: str,
                              side: str, pair: str) -> None:
        base_precision = self._api_precision(info, "basePrecision")
        if self._decimal_places(quantity) > base_precision:
            raise ValueError(f"quantity 超過 {pair} 的 basePrecision={base_precision}")

        if order_type == "LIMIT":
            min_size = self._required_api_limit(info, "minTradeSize")
            max_size = self._required_api_limit(info, "maxTradeSize")
        else:  # 本範本只讓 MARKET SELL 走到這裡
            min_size = self._required_api_limit(info, "minTradeDumping")
            max_size = self._required_api_limit(info, "maxTradeDumping")
        if quantity < min_size:
            raise ValueError(f"quantity 低於 Pionex {pair} 最小值 {min_size}")
        if quantity > max_size:
            raise ValueError(f"quantity 高於 Pionex {pair} 最大值 {max_size}")

        check_price = price
        if order_type == "LIMIT":
            quote_precision = self._api_precision(info, "quotePrecision")
            if price is None or self._decimal_places(price) > quote_precision:
                raise ValueError(f"limit_price 超過 {pair} 的 quotePrecision={quote_precision}")
        elif side == "SELL":
            check_price = Decimal(str(self.get_price(pair)))

        min_amount = self._required_api_limit(info, "minAmount")
        if check_price is None:
            raise RuntimeError("缺少可驗證價格，拒絕檢查 Pionex 最小委託金額")
        if quantity * check_price < min_amount:
            raise ValueError(f"委託金額低於 Pionex {pair} 最小值 {min_amount}")

    @staticmethod
    def _new_client_order_id() -> str:
        return f"agt-{int(time.time() * 1000)}-{secrets.token_hex(5)}"

    def get_account(self) -> AccountInfo:
        balances = self._balance_rows()
        prices = self._spot_prices()
        cash = Decimal("0")
        equity = Decimal("0")
        unpriced = []
        for row in balances:
            coin = str(row.get("coin", "")).upper()
            free = self._api_decimal(row.get("free", "0"), f"{coin} free")
            frozen = self._api_decimal(row.get("frozen", "0"), f"{coin} frozen")
            total = free + frozen
            if total == 0:
                continue
            if coin == self.quote_currency:
                cash += free
                equity += total
                continue
            pair = f"{coin}_{self.quote_currency}"
            price = prices.get(pair)
            if price is None:
                unpriced.append(coin)
            else:
                equity += total * price
        if unpriced:
            raise RuntimeError(
                "無法安全換算帳戶總權益，缺少報價：" + ", ".join(sorted(unpriced))
            )
        return AccountInfo(cash=float(cash), equity=float(equity),
                           currency=self.quote_currency)

    def get_positions(self) -> list[Position]:
        balances = self._balance_rows()
        prices = self._spot_prices()
        positions = []
        for row in balances:
            coin = str(row.get("coin", "")).upper()
            if not coin or coin == self.quote_currency:
                continue
            total = (
                self._api_decimal(row.get("free", "0"), f"{coin} free")
                + self._api_decimal(row.get("frozen", "0"), f"{coin} frozen")
            )
            if total == 0:
                continue
            pair = f"{coin}_{self.quote_currency}"
            price = prices.get(pair)
            if price is None:
                raise RuntimeError(f"無法安全建立部位：{pair} 缺少市價")
            # balance API 沒有成本價；用市價占位以防策略誤判為空手。
            # 不可把這裡的 unrealized_pnl 當真實損益。
            positions.append(Position(
                pair,
                float(total),
                float(price),
                float(price),
                cost_basis_known=False,
            ))
        return positions

    def get_price(self, symbol: str) -> float:
        pair = self._normalise_symbol(symbol)
        payload = self._public_get("/api/v1/market/tickers", {"symbol": pair})
        rows = payload.get("data", {}).get("tickers")
        if not isinstance(rows, list):
            raise RuntimeError("Pionex ticker 回應缺少 data.tickers")
        row = next((item for item in rows if str(item.get("symbol", "")).upper() == pair), None)
        if row is None:
            raise ValueError(f"Pionex 找不到現貨代號 {pair}")
        price = self._api_decimal(row.get("close"), f"{pair} close")
        if price <= 0:
            raise RuntimeError(f"Pionex {pair} 最新價格不是正數")
        return float(price)

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        pair = self._normalise_symbol(order.symbol)
        if order.side == OrderSide.BUY:
            side = "BUY"
        elif order.side == OrderSide.SELL:
            side = "SELL"
        else:  # Order.validate 已擋；保留第二層 fail-closed 防未來介面漂移
            raise ValueError("Pionex 不支援未知委託方向")
        if order.order_type == OrderType.MARKET:
            order_type = "MARKET"
        elif order.order_type == OrderType.LIMIT:
            order_type = "LIMIT"
        else:
            raise ValueError("Pionex 不支援未知委託類型")

        if order_type == "MARKET" and side == "BUY":
            return OrderResult(
                ok=False,
                message=(
                    "Pionex MARKET BUY 必須傳報價幣 amount，但通用 Order.quantity "
                    "代表標的數量；為避免下錯單位，本範本拒絕送單"
                ),
            )

        body = {"symbol": pair, "side": side, "type": order_type}
        # client_tag 是策略標籤（可為中文且可重複），不能拿來當券商唯一委託 ID。
        if order.client_order_id is not None and not isinstance(order.client_order_id, str):
            return OrderResult(
                ok=False,
                message="client_order_id 必須是字串",
            )
        client_order_id = order.client_order_id or self._new_client_order_id()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", client_order_id):
            return OrderResult(
                ok=False,
                message="client_order_id 只能含英數與連字號，最多 64 字",
            )
        body["clientOrderId"] = client_order_id
        quantity = Decimal(self._decimal_text(order.quantity, "quantity"))
        price = (
            Decimal(self._decimal_text(order.limit_price, "limit_price"))
            if order_type == "LIMIT" else None
        )
        try:
            info = self._symbol_info(pair)
            self._validate_order_rules(
                info,
                quantity=quantity,
                price=price,
                order_type=order_type,
                side=side,
                pair=pair,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(ok=False, message=str(exc))
        if order_type == "LIMIT":
            body["size"] = format(quantity, "f")
            body["price"] = format(price, "f")
        else:  # MARKET SELL 的 size 是標的幣數量
            body["size"] = format(quantity, "f")

        try:
            payload = self._private_request(
                "POST", "/api/v1/trade/order", body=body
            )
            data = payload.get("data", {})
            try:
                order_id = str(self._numeric_order_id(data.get("orderId")))
            except (AttributeError, ValueError):
                return OrderResult(
                    ok=False,
                    message=(
                        "Pionex 已回覆 result=true 但缺少 orderId，結果不明；"
                        f"clientOrderId={client_order_id}。先用 "
                        "get_order_by_client_id() 查單，不可直接重送"
                    ),
                    raw=payload,
                )
            self._order_symbols[order_id] = pair
            return OrderResult(ok=True, order_id=order_id, raw=payload)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(
                ok=False,
                message=(
                    f"{exc}；clientOrderId={client_order_id}。若是網路逾時，"
                    "先用 get_order_by_client_id() 查單，不可直接重送"
                ),
            )

    def get_order_by_client_id(self, client_order_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(client_order_id)):
            raise ValueError("client_order_id 只能含英數與連字號，最多 64 字")
        payload = self._private_request(
            "GET",
            "/api/v1/trade/orderByClientOrderId",
            params={"clientOrderId": str(client_order_id)},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Pionex 查單回應缺少 data")
        return data

    @staticmethod
    def _numeric_order_id(order_id: str) -> int:
        value = str(order_id)
        if not re.fullmatch(r"[0-9]+", value):
            raise ValueError("Pionex order_id 必須是正整數")
        numeric = int(value)
        if numeric <= 0:
            raise ValueError("Pionex order_id 必須是正整數")
        return numeric

    def get_order(self, order_id: str) -> dict:
        numeric_order_id = self._numeric_order_id(order_id)
        payload = self._private_request(
            "GET",
            "/api/v1/trade/order",
            params={"orderId": numeric_order_id},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Pionex 查單回應缺少 data")
        return data

    def get_fills_by_order_id(self, order_id: str) -> list[dict]:
        numeric_order_id = self._numeric_order_id(order_id)
        payload = self._private_request(
            "GET",
            "/api/v1/trade/fillsByOrderId",
            params={"orderId": numeric_order_id},
            weight=5,
        )
        fills = payload.get("data", {}).get("fills")
        if not isinstance(fills, list):
            raise RuntimeError("Pionex 成交回應缺少 data.fills")
        return fills

    def cancel_order_details(self, order_id: str,
                             symbol: str | None = None) -> dict:
        """要求撤單後重新查 order / fills；accepted 不代表零成交。"""
        self._guard_live()
        numeric_order_id = self._numeric_order_id(order_id)
        pair = self._normalise_symbol(symbol) if symbol else None
        if pair is None:
            pair = self._order_symbols.get(str(numeric_order_id))
        if pair is None:
            before = self.get_order(str(numeric_order_id))
            pair = self._normalise_symbol(before.get("symbol", ""))

        cancel_response = self._private_request(
            "DELETE",
            "/api/v1/trade/order",
            body={"symbol": pair, "orderId": numeric_order_id},
        )
        try:
            order_after = self.get_order(str(numeric_order_id))
            fills = self.get_fills_by_order_id(str(numeric_order_id))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Pionex 已接受撤單請求，但無法完成撤後 order/fills 覆核；"
                "結果不明，請勿假設零成交"
            ) from exc

        details = {
            "accepted": True,
            "symbol": pair,
            "order_id": str(numeric_order_id),
            "status": order_after.get("status"),
            "filled_size": order_after.get("filledSize"),
            "filled_amount": order_after.get("filledAmount"),
            "fills": fills,
            "order": order_after,
            "cancel_response": cancel_response,
            "warning": "撤單被接受不代表零成交；以 filled_size 與 fills 為準",
        }
        self.last_cancel_details = details
        self._order_symbols.pop(str(numeric_order_id), None)
        return details

    def cancel_order_for_symbol(self, order_id: str, symbol: str) -> bool:
        return bool(self.cancel_order_details(order_id, symbol)["accepted"])

    def cancel_order(self, order_id: str) -> bool:
        return bool(self.cancel_order_details(order_id)["accepted"])
''',
)


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
        self._guard_live()
        import requests
        resp = requests.delete(
            f"{{self.base}}/accounts/{{self.account_id}}/orders/{{order_id}}",
            headers=self._headers())
        # 無條件回 True 會騙人:token 失效/單號不存在時取消其實失敗了
        return resp.ok
''',
)


MORE_BROKER_TEMPLATES = [_PIONEX, _CCXT, _OKX, _BYBIT, _TRADIER]
