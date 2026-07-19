"""Watchlist + alias symbol detection for Narrative Engine."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

DEFAULT_WATCHLIST_PATH = BASE_DIR / "config" / "watchlist.yaml"

_DEFAULT_ASSETS = [
    {"symbol": s, "aliases": [s]}
    for s in (
        "BTC", "ETH", "SOL", "BNB", "XRP", "HYPE", "SUI", "LINK", "AAVE", "ARB",
        "INJ", "AVAX", "DOGE", "ADA", "TRX", "TON", "SEI", "ENA", "OP", "ATOM",
    )
]


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    source_quality: dict[str, float] = {}
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
        if stripped == "source_quality:":
            if current:
                assets.append(current)
                current = None
            section = "source_quality"
            continue
        if section == "assets":
            if stripped.startswith("- symbol:"):
                if current:
                    assets.append(current)
                current = {
                    "symbol": stripped.split(":", 1)[1].strip(),
                    "aliases": [],
                }
            elif stripped.startswith("aliases:") and current is not None:
                rest = stripped.split(":", 1)[1].strip()
                if rest.startswith("[") and rest.endswith("]"):
                    current["aliases"] = [
                        a.strip().strip("'\"")
                        for a in rest[1:-1].split(",")
                        if a.strip()
                    ]
        elif section == "source_quality" and ":" in stripped:
            k, v = stripped.split(":", 1)
            try:
                source_quality[k.strip()] = float(v.strip())
            except ValueError:
                pass
    if current:
        assets.append(current)
    return {"assets": assets, "source_quality": source_quality}


@lru_cache(maxsize=4)
def load_watchlist(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_WATCHLIST_PATH
    if not p.is_file():
        return {
            "assets": list(_DEFAULT_ASSETS),
            "source_quality": {"default": 0.5},
        }
    text = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        if isinstance(data, dict) and data.get("assets"):
            return data
    except Exception:
        pass
    return _parse_simple_yaml(text)


def watched_symbols(path: str | None = None) -> list[str]:
    wl = load_watchlist(path)
    return [
        str(a["symbol"]).upper()
        for a in (wl.get("assets") or [])
        if a.get("symbol")
    ]


def source_quality(source: str, *, path: str | None = None) -> float:
    wl = load_watchlist(path)
    sq = wl.get("source_quality") or {}
    return float(sq.get(source) or sq.get("default") or 0.5)


def _patterns(path: str | None = None) -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = []
    for asset in load_watchlist(path).get("assets") or []:
        sym = str(asset.get("symbol") or "").upper()
        if not sym:
            continue
        aliases = list(asset.get("aliases") or [])
        if sym not in aliases:
            aliases.append(sym)
        aliases_sorted = sorted(
            {a.strip() for a in aliases if a and str(a).strip()},
            key=len,
            reverse=True,
        )
        parts = [re.escape(a) for a in aliases_sorted]
        if not parts:
            continue
        out.append((sym, re.compile(r"\b(" + "|".join(parts) + r")\b", re.I)))
    return out


def detect_symbols(text: str, *, path: str | None = None) -> list[str]:
    found: list[str] = []
    for sym, pat in _patterns(path):
        if pat.search(text or "") and sym not in found:
            found.append(sym)
    return found


def clear_watchlist_cache() -> None:
    load_watchlist.cache_clear()
