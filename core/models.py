"""統一的資料模型。

不論交易資料來自台股、美股還是加密貨幣,不論是 CSV、JSON 還是 API,
最終都會被正規化成這裡定義的 `Trade` 與 `TradeLog`,讓後面的計算邏輯
只需要面對一種格式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Market(str, Enum):
    """市場別。不同市場的交易成本與單位慣例不同。"""

    TW_STOCK = "tw_stock"      # 台灣股市
    US_STOCK = "us_stock"      # 美國股市
    CRYPTO = "crypto"          # 加密貨幣
    UNKNOWN = "unknown"


class Side(str, Enum):
    """方向。做多 / 做空。"""

    LONG = "long"
    SHORT = "short"


@dataclass
class Trade:
    """一筆「已平倉」的完整交易(進場 + 出場)。

    本工具以「已平倉交易」為分析單位 — 因為只有平倉了,
    盈虧才是確定的。未平倉的部位是浮動的,不納入勝率與盈虧比計算。

    Attributes:
        symbol:       標的代號(如 2330、AAPL、BTCUSDT)
        market:       市場別
        side:         做多或做空
        entry_time:   進場時間
        exit_time:    出場時間
        entry_price:  進場價(每單位)
        exit_price:   出場價(每單位)
        quantity:     數量(股數 / 張數 / 幣數)
        fees:         此筆交易的總成本(手續費 + 稅 + 滑價),已知則填,未知留 0
        pnl:          盈虧金額。若提供則直接採用;否則由價格與數量推算
        tag:          使用者自訂的策略標籤(如 "突破", "均線多頭"),用於反推交易邏輯
    """

    symbol: str
    market: Market
    side: Side
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    fees: float = 0.0
    pnl: Optional[float] = None
    tag: Optional[str] = None

    def __post_init__(self) -> None:
        # 若使用者沒提供 pnl,就用價格推算。做多與做空的方向相反。
        if self.pnl is None:
            gross = (self.exit_price - self.entry_price) * self.quantity
            if self.side == Side.SHORT:
                gross = -gross
            self.pnl = gross - self.fees

    @property
    def is_win(self) -> bool:
        """這筆交易是否獲利(扣除成本後 pnl > 0)。"""
        return (self.pnl or 0.0) > 0

    @property
    def return_pct(self) -> float:
        """報酬率(相對於進場投入的本金)。用於 R-multiple 與風險衡量。"""
        cost_basis = abs(self.entry_price * self.quantity)
        if cost_basis == 0:
            return 0.0
        return (self.pnl or 0.0) / cost_basis

    @property
    def holding_days(self) -> float:
        """持倉天數。用於區分長期投資 vs 當沖/短線投機。"""
        delta = self.exit_time - self.entry_time
        return delta.total_seconds() / 86400.0


@dataclass
class TradeLog:
    """一整份交易紀錄,通常代表一個策略或一個帳戶的歷史。"""

    trades: list[Trade] = field(default_factory=list)
    source: str = ""           # 資料來源說明(檔名 / API 名稱)
    account_label: str = ""    # 帳戶或策略標籤

    def __len__(self) -> int:
        return len(self.trades)

    def __iter__(self):
        return iter(self.trades)

    @property
    def markets(self) -> set[Market]:
        return {t.market for t in self.trades}

    def filter_by_tag(self, tag: str) -> "TradeLog":
        """只取某個策略標籤的交易,用於分策略評估。"""
        return TradeLog(
            trades=[t for t in self.trades if t.tag == tag],
            source=self.source,
            account_label=f"{self.account_label}::{tag}",
        )

    def sorted_by_time(self) -> "TradeLog":
        """依出場時間排序。回測與回撤計算需要時間順序。"""
        return TradeLog(
            trades=sorted(self.trades, key=lambda t: t.exit_time),
            source=self.source,
            account_label=self.account_label,
        )
