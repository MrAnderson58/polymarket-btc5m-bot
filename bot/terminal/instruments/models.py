"""Instrument catalog models — metadata only, no trading logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    CRYPTO_FUTURE = "crypto_future"
    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"
    COMMODITY = "commodity"
    FOREX = "forex"


AssetClassName = Literal[
    "crypto",
    "crypto_future",
    "stock",
    "etf",
    "index",
    "commodity",
    "forex",
]


@dataclass(frozen=True)
class Instrument:
    """Canonical instrument descriptor for Terminal / future adapters."""

    symbol: str
    name: str
    asset_class: AssetClass
    exchange: str
    currency: str
    tradable: bool = True
    tick_size: float = 0.01
    price_precision: int = 2
    quantity_precision: int = 3

    def normalized_symbol(self) -> str:
        return self.symbol.upper().replace("/", "").replace("-", "")


__all__ = ["AssetClass", "AssetClassName", "Instrument"]
