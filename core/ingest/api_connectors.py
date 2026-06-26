"""券商 / 交易所 API 連接器(抽象介面 + 範例實作)。

設計原則:本工具只「讀取」歷史交易紀錄做分析,絕不下單。
因此這裡的連接器一律是「唯讀」的 — 即使串接真實 API,
也只呼叫查詢歷史成交的端點,從不呼叫下單端點。

每個連接器負責把該平台的原始回應,轉成 TradeLog。
真實串接需要使用者自行填入 API key,並安裝對應 SDK。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import TradeLog


class ReadOnlyConnector(ABC):
    """唯讀交易紀錄連接器的共同介面。

    刻意「不」提供任何下單方法 — 這是設計上的安全護欄,
    確保這個工具永遠不會在使用者帳戶上產生真實交易。
    """

    #: 連接器的人類可讀名稱
    name: str = "abstract"

    @abstractmethod
    def fetch_closed_trades(self, **kwargs) -> TradeLog:
        """抓取「已平倉」的歷史交易,回傳正規化的 TradeLog。"""
        raise NotImplementedError


class BinanceReadOnlyConnector(ReadOnlyConnector):
    """Binance 現貨已成交紀錄(唯讀)。

    需安裝 `python-binance` 並提供「唯讀」API key
    (在 Binance 後台建立 key 時請取消勾選交易與提領權限)。

    用法:
        conn = BinanceReadOnlyConnector(api_key, api_secret)
        log = conn.fetch_closed_trades(symbols=["BTCUSDT", "ETHUSDT"])
    """

    name = "binance"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret

    def fetch_closed_trades(self, symbols: list[str] | None = None, **kwargs) -> TradeLog:
        raise NotImplementedError(
            "Binance 連接器為範例骨架。實作步驟:\n"
            "1. pip install python-binance\n"
            "2. 建立「唯讀」API key(關閉交易/提領權限)\n"
            "3. 用 client.get_my_trades(symbol=...) 抓成交,自行配對買賣成來回交易\n"
            "4. 轉成 core.models.Trade 並組成 TradeLog\n"
            "本檔刻意不內建真實金鑰呼叫,以免誤觸下單。"
        )


class IBKRReadOnlyConnector(ReadOnlyConnector):
    """Interactive Brokers 已成交紀錄(唯讀,涵蓋美股)。

    需安裝 `ib_insync` 並開啟 TWS / IB Gateway 的唯讀 API。
    """

    name = "ibkr"

    def fetch_closed_trades(self, **kwargs) -> TradeLog:
        raise NotImplementedError(
            "IBKR 連接器為範例骨架。建議用 ib_insync 的 reqExecutions/fills,\n"
            "並在 TWS 設定中啟用『Read-Only API』以杜絕下單風險。"
        )


# 連接器註冊表:CLI / Skill 依名稱取用
CONNECTORS: dict[str, type[ReadOnlyConnector]] = {
    "binance": BinanceReadOnlyConnector,
    "ibkr": IBKRReadOnlyConnector,
}
