"""券商下單抽象層。

設計核心:
  - 一個統一的 BrokerAdapter 介面,讓策略程式不必在意背後是哪家券商。
  - 預設用 PaperBroker(紙上模擬),不碰真錢。
  - 真實券商一律是「待填框架」 — 交易者自己填 API key 與下單實作,自負風險。
  - 內建「下單安全閘門」:除非交易者明確解除,否則真實下單會被攔下。
"""

from .base import (
    AccountInfo,
    BrokerAdapter,
    Order,
    OrderResult,
    OrderSide,
    OrderType,
    Position,
)
from .paper import PaperBroker
from .registry import BROKER_TEMPLATES, list_brokers

__all__ = [
    "BrokerAdapter",
    "Order",
    "OrderResult",
    "OrderSide",
    "OrderType",
    "Position",
    "AccountInfo",
    "PaperBroker",
    "BROKER_TEMPLATES",
    "list_brokers",
]
