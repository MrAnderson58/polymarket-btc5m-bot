"""S44 — load multi-source YAML configs (minimal parser, no PyYAML required)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from bot.research.market_events.config import BASE_DIR

CONFIG_DIR = BASE_DIR / "config"


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if s.lower() in ("true", "yes"):
        return True
    if s.lower() in ("false", "no"):
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s.strip("'\"")


def _parse_list_yaml(text: str) -> list[dict[str, Any]]:
    """Parse `sources:` list of maps with simple nested keys."""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_sources = False
    for raw in text.splitlines():
        if "#" in raw:
            raw = raw.split("#", 1)[0]
        if not raw.strip():
            continue
        if raw.strip() == "sources:":
            in_sources = True
            continue
        if not in_sources:
            continue
        # New list item
        if raw.lstrip().startswith("- "):
            if current:
                items.append(current)
            current = {}
            rest = raw.lstrip()[2:]
            if ":" in rest:
                k, v = rest.split(":", 1)
                current[k.strip()] = _parse_scalar(v)
            continue
        if current is None:
            continue
        if ":" in raw:
            k, v = raw.strip().split(":", 1)
            current[k.strip()] = _parse_scalar(v)
    if current:
        items.append(current)
    return items


@lru_cache(maxsize=16)
def load_sources_config(name: str) -> list[dict[str, Any]]:
    """Load config/<name>.yaml sources list."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.is_file():
        return []
    return _parse_list_yaml(path.read_text(encoding="utf-8"))


def clear_sources_cache() -> None:
    load_sources_config.cache_clear()


def enabled_sources(name: str) -> list[dict[str, Any]]:
    rows = load_sources_config(name)
    out = [r for r in rows if r.get("enabled", True)]
    out.sort(key=lambda r: int(r.get("priority") or 100))
    return out
