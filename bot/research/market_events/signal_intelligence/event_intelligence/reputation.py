"""S43 Event Intelligence — source reputation loader."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

DEFAULT_REPUTATION_PATH = BASE_DIR / "config" / "source_reputation.yaml"

_DEFAULTS: dict[str, float] = {
    "Reuters": 1.00,
    "Bloomberg": 0.99,
    "CoinDesk": 0.96,
    "The Block": 0.95,
    "Blockworks": 0.94,
    "Decrypt": 0.92,
    "Wu Blockchain": 0.91,
    "Cointelegraph": 0.89,
    "CryptoSlate": 0.85,
    "Telegram": 0.75,
    "Unknown": 0.40,
}


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML subset: sources map + default scalar."""
    sources: dict[str, float] = {}
    default = 0.40
    in_sources = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.strip() == "sources:":
            in_sources = True
            continue
        if line.startswith("default:"):
            in_sources = False
            try:
                default = float(line.split(":", 1)[1].strip())
            except Exception:
                pass
            continue
        if in_sources and ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lstrip("- ").strip()
            try:
                sources[key] = float(val.strip())
            except Exception:
                continue
    return {"sources": sources, "default": default}


@lru_cache(maxsize=4)
def load_source_reputation(
    path: str | None = None,
) -> tuple[dict[str, float], float]:
    p = Path(path) if path else DEFAULT_REPUTATION_PATH
    if p.is_file():
        data = _parse_simple_yaml(p.read_text(encoding="utf-8"))
        sources = {**_DEFAULTS, **(data.get("sources") or {})}
        default = float(data.get("default") or 0.40)
        return sources, default
    return dict(_DEFAULTS), 0.40


def clear_reputation_cache() -> None:
    load_source_reputation.cache_clear()


def source_reputation(name: str, *, path: str | None = None) -> float:
    sources, default = load_source_reputation(path)
    if not name:
        return default
    if name in sources:
        return float(sources[name])
    # Case-insensitive / partial match for Wu Blockchain etc.
    lower = name.lower()
    for key, val in sources.items():
        if key.lower() == lower:
            return float(val)
        if key.lower() in lower or lower in key.lower():
            return float(val)
    return default
