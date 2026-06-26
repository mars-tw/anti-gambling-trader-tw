"""匯入層:把各種來源的交易資料正規化成 TradeLog。"""

from .loader import load_trades, sniff_format

__all__ = ["load_trades", "sniff_format"]
