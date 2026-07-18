"""JSON persistence for per-user watchlists."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from bot.terminal.watchlist.models import Watchlist, WatchlistItem, normalize_symbol


def default_watchlist_path() -> Path:
    try:
        from bot.research.futures_agent.env_bootstrap import project_root

        root = project_root()
    except Exception:
        root = Path(__file__).resolve().parents[3]
    return root / "data" / "terminal" / "watchlists.json"


class WatchlistStore:
    """Atomic JSON file store: { user_id: [symbols...] }."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_watchlist_path()

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> dict[str, list[str]]:
        if not self._path.is_file():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, list[str]] = {}
        for uid, symbols in raw.items():
            if not isinstance(symbols, list):
                continue
            cleaned: list[str] = []
            seen: set[str] = set()
            for s in symbols:
                key = normalize_symbol(str(s))
                if key and key not in seen:
                    seen.add(key)
                    cleaned.append(key)
            out[str(uid)] = cleaned
        return out

    def save_all(self, data: dict[str, list[str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)

    def get(self, user_id: str) -> Watchlist | None:
        data = self.load_all()
        uid = str(user_id)
        if uid not in data:
            return None
        items = tuple(WatchlistItem(symbol=s) for s in data[uid])
        return Watchlist(user_id=uid, items=items)

    def put(self, watchlist: Watchlist) -> None:
        data = self.load_all()
        data[str(watchlist.user_id)] = list(watchlist.symbols())
        self.save_all(data)

    def delete(self, user_id: str) -> None:
        data = self.load_all()
        if str(user_id) in data:
            del data[str(user_id)]
            self.save_all(data)


def watchlist_to_payload(watchlist: Watchlist) -> dict[str, Any]:
    return {"user_id": watchlist.user_id, "symbols": list(watchlist.symbols())}


__all__ = [
    "WatchlistStore",
    "default_watchlist_path",
    "watchlist_to_payload",
]
