"""S41 watchlist loader + symbol alias detection."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

DEFAULT_WATCHLIST_PATH = BASE_DIR / "config" / "watchlist.yaml"

_DEFAULT_ASSETS: list[dict[str, Any]] = [
    {"symbol": "BTC", "aliases": ["Bitcoin", "BTC"]},
    {"symbol": "ETH", "aliases": ["Ethereum", "Ether", "ETH"]},
    {"symbol": "SOL", "aliases": ["Solana", "SOL"]},
    {"symbol": "BNB", "aliases": ["BNB", "Binance Coin"]},
    {"symbol": "XRP", "aliases": ["XRP", "Ripple"]},
    {"symbol": "HYPE", "aliases": ["HYPE", "Hyperliquid"]},
    {"symbol": "SUI", "aliases": ["SUI", "Sui"]},
    {"symbol": "LINK", "aliases": ["LINK", "Chainlink"]},
    {"symbol": "AAVE", "aliases": ["AAVE", "Aave"]},
    {"symbol": "ARB", "aliases": ["ARB", "Arbitrum"]},
    {"symbol": "INJ", "aliases": ["INJ", "Injective"]},
    {"symbol": "AVAX", "aliases": ["AVAX", "Avalanche"]},
    {"symbol": "DOGE", "aliases": ["DOGE", "Dogecoin"]},
    {"symbol": "ADA", "aliases": ["ADA", "Cardano"]},
    {"symbol": "TRX", "aliases": ["TRX", "Tron", "TRON"]},
    {"symbol": "TON", "aliases": ["TON", "Toncoin"]},
    {"symbol": "SEI", "aliases": ["SEI", "Sei"]},
    {"symbol": "ENA", "aliases": ["ENA", "Ethena"]},
    {"symbol": "OP", "aliases": ["OP", "Optimism"]},
    {"symbol": "ATOM", "aliases": ["ATOM", "Cosmos"]},
]


def _parse_simple_watchlist_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML subset parser (assets list) — no PyYAML required."""
    assets: list[dict[str, Any]] = []
    source_types: dict[str, str] = {}
    section: str | None = None
    current: dict[str, Any] | None = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "assets:":
            section = "assets"
            continue
        if stripped == "source_types:":
            if current:
                assets.append(current)
                current = None
            section = "source_types"
            continue
        if section == "assets":
            if stripped.startswith("- symbol:"):
                if current:
                    assets.append(current)
                sym = stripped.split(":", 1)[1].strip()
                current = {"symbol": sym, "aliases": []}
            elif stripped.startswith("aliases:") and current is not None:
                rest = stripped.split(":", 1)[1].strip()
                if rest.startswith("[") and rest.endswith("]"):
                    inner = rest[1:-1]
                    aliases = [a.strip().strip("'\"") for a in inner.split(",") if a.strip()]
                    current["aliases"] = aliases
            elif stripped.startswith("symbol:") and current is not None:
                current["symbol"] = stripped.split(":", 1)[1].strip()
        elif section == "source_types" and ":" in stripped:
            k, v = stripped.split(":", 1)
            source_types[k.strip()] = v.strip()

    if current:
        assets.append(current)
    return {"assets": assets, "source_types": source_types}


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _parse_simple_watchlist_yaml(text)


@lru_cache(maxsize=4)
def load_watchlist(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_WATCHLIST_PATH
    if not p.is_file():
        return {"assets": list(_DEFAULT_ASSETS), "source_types": {"default": "rss"}}
    data = _load_yaml(p)
    assets = data.get("assets") or list(_DEFAULT_ASSETS)
    return {
        "assets": assets,
        "source_types": dict(data.get("source_types") or {"default": "rss"}),
    }


def watched_symbols(path: str | None = None) -> list[str]:
    wl = load_watchlist(path)
    return [str(a["symbol"]).upper() for a in wl.get("assets") or [] if a.get("symbol")]


def _alias_patterns(path: str | None = None) -> list[tuple[str, re.Pattern[str]]]:
    wl = load_watchlist(path)
    out: list[tuple[str, re.Pattern[str]]] = []
    for asset in wl.get("assets") or []:
        sym = str(asset.get("symbol") or "").upper()
        if not sym:
            continue
        aliases = list(asset.get("aliases") or [])
        if sym not in aliases:
            aliases.append(sym)
        # Longer aliases first to prefer multi-word names over tickers.
        aliases_sorted = sorted(
            {a.strip() for a in aliases if a and str(a).strip()},
            key=len,
            reverse=True,
        )
        parts = [re.escape(a) for a in aliases_sorted]
        if not parts:
            continue
        pat = re.compile(r"\b(" + "|".join(parts) + r")\b", re.IGNORECASE)
        out.append((sym, pat))
    return out


def detect_symbols(text: str, *, path: str | None = None) -> list[str]:
    """Detect watchlist symbols from free text via aliases."""
    blob = text or ""
    found: list[str] = []
    for sym, pat in _alias_patterns(path):
        if pat.search(blob) and sym not in found:
            found.append(sym)
    return found


def source_type_for(source: str, *, path: str | None = None) -> str:
    wl = load_watchlist(path)
    st = wl.get("source_types") or {}
    return str(st.get(source) or st.get("default") or "rss")


def clear_watchlist_cache() -> None:
    load_watchlist.cache_clear()
