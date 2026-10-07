"""統一的資料模型。

不論交易資料來自台股、美股還是加密貨幣,不論是 CSV、JSON 還是 API,
最終都會被正規化成這裡定義的 `Trade` 與 `TradeLog`,讓後面的計算邏輯
只需要面對一種格式。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class Market(str, Enum):
    """市場別。不同市場的交易成本與單位慣例不同。"""

    TW_STOCK = "tw_stock"        # 台灣股市
    TW_ETF = "tw_etf"            # 台股 ETF(證交稅 0.1%,非 0.3%)
    US_STOCK = "us_stock"        # 美國股市
    CRYPTO = "crypto"            # 加密貨幣
    TW_FUTURES = "tw_futures"    # 台指期等(單位「口」,有契約乘數)
    TW_OPTIONS = "tw_options"    # 台指選擇權(權利金 × 乘數)
    FOREX = "forex"              # 外匯 / 差價合約
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
        quantity:     數量(股數 / 口數 / 幣數)
        fees:         此筆交易的總成本(手續費 + 稅 + 滑價),已知則填,未知留 0
        pnl:          盈虧金額。若提供則直接採用;否則由價格與數量推算
        tag:          使用者自訂的策略標籤(如 "突破", "均線多頭"),用於反推交易邏輯
        contract_multiplier:
                      契約乘數。股票 / 加密貨幣為 1.0;台指期 200、小台 50、
                      台指選擇權 50。**不乘這個數字,期貨損益會少算 200 倍。**
                      預設 1.0,因此舊有的股票 / 加密貨幣資料行為完全不變。
        entry_time_known / exit_time_known:
                      原始資料是否真的提供進/出場時間。loader 為了保持
                      datetime 型別會使用佔位值，但旗標為 False 時，任何
                      當沖、持倉天數或時間趨勢都不得使用該佔位值。
        side_known:   原始資料是否明示做多/做空。直接淨損益在方向缺漏時仍可
                      做金額統計，但不得拿預設值反推方向偏好或策略。
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
    contract_multiplier: float = 1.0
    entry_time_known: bool = True
    exit_time_known: bool = True
    contract_multiplier_known: bool = True
    notional_reliable: bool = True
    pnl_currency: Optional[str] = None
    side_known: bool = True
    entry_local_date: Optional[date] = None
    exit_local_date: Optional[date] = None
    pnl_is_direct: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        numeric_fields = (
            ("entry_price", self.entry_price),
            ("exit_price", self.exit_price),
            ("quantity", self.quantity),
            ("fees", self.fees),
            ("contract_multiplier", self.contract_multiplier),
        )
        if self.pnl is not None:
            numeric_fields += (("pnl", self.pnl),)
        for name, value in numeric_fields:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{name} 必須是有限數字（bool 不算金融數值）")

        self.pnl_is_direct = self.pnl is not None
        if (
            self.market in (Market.TW_FUTURES, Market.TW_OPTIONS)
            and self.contract_multiplier == 1.0
            and self.contract_multiplier_known
        ):
            # 公開 analyze_log API 可能繞過 loader；台期乘數預設 1 會把損益
            # 算錯 10~4000 倍。未明示乘數時一律降級，價差推算則直接拒絕。
            self.contract_multiplier_known = False
            self.notional_reliable = False
            if not self.pnl_is_direct:
                raise ValueError(
                    f"{self.symbol}: 台灣期貨/選擇權必須明示正確 contract_multiplier，"
                    "或提供券商 direct net pnl"
                )
        # 不變量:出場不可早於進場。這種列是髒資料(或欄位對錯),
        # 靜默收下會產生負持倉天數、被 profiler 誤分類成短線 —— 直接拒絕。
        if (
            self.entry_time_known
            and self.exit_time_known
            and not self.time_basis_consistent
        ):
            raise ValueError(
                f"{self.symbol}: 同一筆交易的進出場時間混用了有時區與無時區格式，"
                "請先統一時區基準"
            )
        if self.time_basis_consistent and self.exit_time < self.entry_time:
            raise ValueError(
                f"{self.symbol}: 出場時間({self.exit_time:%Y-%m-%d %H:%M})早於"
                f"進場時間({self.entry_time:%Y-%m-%d %H:%M}),資料有誤"
            )
        # 若使用者沒提供 pnl,就用價格推算。做多與做空的方向相反。
        # 契約乘數必須納入 —— 否則台指期(乘數 200)的損益會少算 200 倍。
        if self.pnl is None:
            gross = (
                (self.exit_price - self.entry_price)
                * self.quantity
                * self.contract_multiplier
            )
            if self.side == Side.SHORT:
                gross = -gross
            computed_pnl = gross - self.fees
            if not math.isfinite(computed_pnl):
                raise ValueError("由價差計算出的 pnl 不是有限數字")
            self.pnl = computed_pnl

    @property
    def is_win(self) -> bool:
        """這筆交易是否獲利(扣除成本後 pnl > 0)。"""
        return (self.pnl or 0.0) > 0

    @property
    def contract_value(self) -> float:
        """進場時的契約價值(股票即市值;期貨為 價格 × 乘數 × 口數)。"""
        if not self.notional_reliable or not self.contract_multiplier_known:
            return 0.0
        return abs(self.entry_price * self.quantity * self.contract_multiplier)

    @property
    def return_pct(self) -> float:
        """報酬率(相對於進場的契約價值)。

        注意:槓桿商品(期貨/選擇權/外匯)的母體是「契約價值」而非「保證金」。
        以保證金為母體算出的報酬率會高出數十倍,兩者不可互相比較。
        我們刻意選擇契約價值 —— 因為保證金比例因券商與帳戶而異,
        憑空假設一個比例就是假精準。
        """
        basis = self.contract_value
        if basis == 0:
            return 0.0
        return (self.pnl or 0.0) / basis

    @property
    def time_basis_consistent(self) -> bool:
        """進出場時間皆已知，且同為 aware 或同為 naive datetime。"""
        if not (self.entry_time_known and self.exit_time_known):
            return False

        def is_aware(value: datetime) -> bool:
            return value.tzinfo is not None and value.utcoffset() is not None

        return is_aware(self.entry_time) == is_aware(self.exit_time)

    @property
    def holding_days(self) -> float | None:
        """持倉天數；時間缺漏或時區基準不一致時回傳 None。"""
        if not self.time_basis_consistent:
            return None
        delta = self.exit_time - self.entry_time
        return delta.total_seconds() / 86400.0

    @property
    def is_day_trade(self) -> bool:
        """是否為當沖:進出場在『同一交易日』(同一日曆日)。

        全工具統一用這個定義(而非 holding_days < 1),避免成本估算
        (台股當沖證交稅減半)與風格判定兩處各自為政:跨夜但 < 24h 的
        短單若用 holding_days < 1 會被當沖,但用日曆日卻不是,造成不一致。
        """
        return (
            self.time_basis_consistent
            and (self.entry_local_date or self.entry_time.date())
            == (self.exit_local_date or self.exit_time.date())
        )


@dataclass
class TradeLog:
    """一整份交易紀錄,通常代表一個策略或一個帳戶的歷史。"""

    trades: list[Trade] = field(default_factory=list)
    source: str = ""           # 資料來源說明(檔名 / API 名稱)
    account_label: str = ""    # 帳戶或策略標籤
    rejected_row_count: int = 0
    rejected_row_reasons: tuple[str, ...] = field(default_factory=tuple)
    suspected_duplicate_count: int = 0
    _inherited_duplicate_hold: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.rejected_row_count, bool)
            or not isinstance(self.rejected_row_count, int)
            or self.rejected_row_count < 0
        ):
            raise ValueError("rejected_row_count 必須是非負整數")
        if (
            isinstance(self.suspected_duplicate_count, bool)
            or not isinstance(self.suspected_duplicate_count, int)
            or self.suspected_duplicate_count < 0
        ):
            raise ValueError("suspected_duplicate_count 必須是非負整數")
        # 原因只保留有限預覽，完整筆數由 rejected_row_count 表示；這避免
        # 巨型髒檔把報告/JSON 撐爆，同時不會把被拒筆數靜默抹掉。
        self.rejected_row_reasons = tuple(
            str(reason) for reason in self.rejected_row_reasons[:10]
        )
        # 此欄是來源或父 TradeLog 已知的完整性下限；不可因 filter / sort
        # 把重複列從眼前清掉就當成已完成修復。真正修復後應建立新的 TradeLog
        # 重新稽核原始資料，而不是原地抹除風險旗標。
        self._inherited_duplicate_hold = self.suspected_duplicate_count
        self.refresh_integrity()

    def _detect_exact_duplicate_count(self) -> int:
        """計算完整且時間已知之交易中，超出第一筆的精確重複列數。"""

        seen: set[tuple] = set()
        duplicates = 0
        for trade in self.trades:
            if not (
                getattr(trade, "entry_time_known", True)
                and getattr(trade, "exit_time_known", True)
            ):
                # pnl-only 佔位時間相同不代表同一筆交易，不能據此誤判重複。
                continue
            key = (
                trade.symbol,
                trade.market,
                trade.side,
                trade.entry_time,
                trade.exit_time,
                trade.entry_price,
                trade.exit_price,
                trade.quantity,
                trade.fees,
                trade.pnl,
                repr(trade.tag),
                trade.contract_multiplier,
                trade.entry_time_known,
                trade.exit_time_known,
                trade.contract_multiplier_known,
                trade.notional_reliable,
                trade.pnl_currency,
                trade.side_known,
                trade.entry_local_date,
                trade.exit_local_date,
                trade.pnl_is_direct,
            )
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
        return duplicates

    def refresh_integrity(self) -> int:
        """重新檢查可變 trades，保留繼承或已觀測到的重複風險下限。"""
        detected = self._detect_exact_duplicate_count()
        self._inherited_duplicate_hold = max(
            self._inherited_duplicate_hold,
            detected,
        )
        self.suspected_duplicate_count = self._inherited_duplicate_hold
        return self.suspected_duplicate_count

    @property
    def integrity_complete(self) -> bool:
        self.refresh_integrity()
        return self.rejected_row_count == 0 and self.suspected_duplicate_count == 0

    def integrity_as_dict(self) -> dict:
        self.refresh_integrity()
        return {
            "status": "complete" if self.integrity_complete else "incomplete",
            "complete": self.integrity_complete,
            "rejected_row_count": self.rejected_row_count,
            "rejected_row_reasons": list(self.rejected_row_reasons),
            "suspected_duplicate_count": self.suspected_duplicate_count,
        }

    def __len__(self) -> int:
        return len(self.trades)

    def __iter__(self):
        return iter(self.trades)

    @property
    def markets(self) -> set[Market]:
        return {t.market for t in self.trades}

    def filter_by_tag(self, tag: str) -> "TradeLog":
        """只取某個策略標籤的交易,用於分策略評估。"""
        self.refresh_integrity()
        return TradeLog(
            trades=[t for t in self.trades if t.tag == tag],
            source=self.source,
            account_label=f"{self.account_label}::{tag}",
            rejected_row_count=self.rejected_row_count,
            rejected_row_reasons=self.rejected_row_reasons,
            suspected_duplicate_count=self.suspected_duplicate_count,
        )

    def filter(self, predicate, label: str = "filtered") -> "TradeLog":
        """用任意述詞篩選交易(如『跟單類 tag』或『排除最差策略』)。"""
        self.refresh_integrity()
        return TradeLog(
            trades=[t for t in self.trades if predicate(t)],
            source=self.source,
            account_label=f"{self.account_label}::{label}",
            rejected_row_count=self.rejected_row_count,
            rejected_row_reasons=self.rejected_row_reasons,
            suspected_duplicate_count=self.suspected_duplicate_count,
        )

    def sorted_by_time(self) -> "TradeLog":
        """依出場時間排序。回測與回撤計算需要時間順序。"""
        self.refresh_integrity()
        return TradeLog(
            trades=sorted(self.trades, key=lambda t: t.exit_time),
            source=self.source,
            account_label=self.account_label,
            rejected_row_count=self.rejected_row_count,
            rejected_row_reasons=self.rejected_row_reasons,
            suspected_duplicate_count=self.suspected_duplicate_count,
        )
