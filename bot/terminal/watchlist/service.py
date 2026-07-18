"""WatchlistService — per-user favorites API."""

from __future__ import annotations

from pathlib import Path

from bot.terminal.watchlist.models import (
    Watchlist,
    WatchlistItem,
    default_watchlist,
    normalize_symbol,
)
from bot.terminal.watchlist.persistence import WatchlistStore


class WatchlistService:
    def __init__(self, store: WatchlistStore | None = None) -> None:
        self._store = store or WatchlistStore()

    def get(self, user_id: str | int, *, seed_defaults: bool = True) -> Watchlist:
        uid = str(user_id)
        existing = self._store.get(uid)
        if existing is not None:
            return existing
        wl = default_watchlist(uid) if seed_defaults else Watchlist(user_id=uid, items=())
        if seed_defaults:
            self._store.put(wl)
        return wl

    def list_symbols(self, user_id: str | int) -> tuple[str, ...]:
        return self.get(user_id).symbols()

    def is_favorite(self, user_id: str | int, symbol: str) -> bool:
        return self.get(user_id).contains(symbol)

    def add(self, user_id: str | int, symbol: str) -> Watchlist:
        uid = str(user_id)
        key = normalize_symbol(symbol)
        if not key:
            raise ValueError("empty symbol")
        wl = self.get(uid)
        if wl.contains(key):
            return wl
        items = list(wl.items) + [WatchlistItem(symbol=key)]
        updated = wl.with_items(items)
        self._store.put(updated)
        return updated

    def remove(self, user_id: str | int, symbol: str) -> Watchlist:
        uid = str(user_id)
        key = normalize_symbol(symbol)
        wl = self.get(uid)
        items = [i for i in wl.items if i.symbol != key]
        updated = wl.with_items(items)
        self._store.put(updated)
        return updated

    def toggle(self, user_id: str | int, symbol: str) -> tuple[Watchlist, bool]:
        """Toggle favorite. Returns (watchlist, now_favorited)."""
        key = normalize_symbol(symbol)
        if self.is_favorite(user_id, key):
            return self.remove(user_id, key), False
        return self.add(user_id, key), True


_default_service: WatchlistService | None = None


def get_watchlist_service(*, path: Path | None = None) -> WatchlistService:
    global _default_service
    if path is not None:
        return WatchlistService(WatchlistStore(path))
    if _default_service is None:
        _default_service = WatchlistService()
    return _default_service


def reset_watchlist_service() -> None:
    """Test helper — clear process-wide singleton."""
    global _default_service
    _default_service = None


__all__ = [
    "WatchlistService",
    "get_watchlist_service",
    "reset_watchlist_service",
]
