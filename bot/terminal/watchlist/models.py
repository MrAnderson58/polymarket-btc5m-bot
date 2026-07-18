"""Watchlist DTOs — per-user favorites."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


DEFAULT_FAVORITES: tuple[str, ...] = ("BTC", "ETH", "NVDA", "TSLA", "GOLD")


@dataclass(frozen=True)
class WatchlistItem:
    symbol: str
    label: str | None = None

    def display(self) -> str:
        return self.label or self.symbol


@dataclass(frozen=True)
class Watchlist:
    """Favorites list for one user."""

    user_id: str
    items: tuple[WatchlistItem, ...] = ()

    def symbols(self) -> tuple[str, ...]:
        return tuple(i.symbol for i in self.items)

    def contains(self, symbol: str) -> bool:
        key = normalize_symbol(symbol)
        return any(i.symbol == key for i in self.items)

    def with_items(self, items: Sequence[WatchlistItem]) -> Watchlist:
        return Watchlist(user_id=self.user_id, items=tuple(items))


def normalize_symbol(raw: str) -> str:
    """Canonical watchlist symbol (BTC, GOLD, …)."""
    s = (raw or "").strip().upper().replace("/", "").replace("-", "").replace(" ", "")
    aliases = {
        "XAU": "GOLD",
        "XAUUSD": "GOLD",
        "GLD": "GOLD",
        "BITCOIN": "BTC",
        "ETHEREUM": "ETH",
        "BTCUSDT": "BTC",
        "ETHUSDT": "ETH",
        "SOLUSDT": "SOL",
    }
    return aliases.get(s, s)


def default_watchlist(user_id: str) -> Watchlist:
    return Watchlist(
        user_id=str(user_id),
        items=tuple(WatchlistItem(symbol=s) for s in DEFAULT_FAVORITES),
    )


__all__ = [
    "DEFAULT_FAVORITES",
    "Watchlist",
    "WatchlistItem",
    "default_watchlist",
    "normalize_symbol",
]
