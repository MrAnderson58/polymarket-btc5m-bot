"""Persist Trading Intelligence output for read-only Report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR

CACHE_DIR = BASE_DIR / "intelligence_cache"
RESULTS_PATH = CACHE_DIR / "intelligence_results.json"

CACHE_UNAVAILABLE_MSG = (
    "Trading Intelligence cache unavailable.\n\nRun:\n\npython -m bot.daily"
)

INTELLIGENCE_KEYS = (
    "execution_audit",
    "stop_quality",
    "recovery_analyzer",
    "false_stop_detector",
    "regime_engine",
    "signal_quality",
    "feature_drift",
    "regime_change_detector",
    "capital_simulator",
)


def _empty_sections(*, note: str | None = None) -> dict[str, Any]:
    return {
        "execution_audit": {"trades": [], "summary": {}},
        "stop_quality": {"total_stops": 0, "rating_counts": {}, "stops": []},
        "recovery_analyzer": {"analyzed_stops": 0, "alternative_holds": [], "trades": []},
        "false_stop_detector": {
            "total_stops": 0,
            "false_stops": 0,
            "true_stops": 0,
            "late_stops": 0,
            "false_stop_rate": 0.0,
            "examples": [],
        },
        "regime_engine": {"regimes": {}, "best_regime": None, "worst_regime": None},
        "signal_quality": {"trades_scored": 0, "samples": []},
        "feature_drift": {"status": "unavailable", "alerts": []},
        "regime_change_detector": {"changed": False, "level": "LOW", "message": note or ""},
        "capital_simulator": {"scenarios": [], "primary_path_100usd": ""},
    }


def is_cache_available(cache: dict[str, Any]) -> bool:
    meta = cache.get("meta", {})
    if meta.get("source") in ("empty", "corrupted"):
        return False
    return bool(meta.get("generated_at") or meta.get("cached_at"))


def save_intelligence_cache(sections: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "cache_version": "1.0",
            "source": "daily",
        },
        **{key: sections.get(key, {}) for key in INTELLIGENCE_KEYS},
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return RESULTS_PATH


def load_intelligence_cache() -> dict[str, Any]:
    if not RESULTS_PATH.exists():
        return {"meta": {"source": "empty"}, **_empty_sections()}
    try:
        return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "meta": {"source": "corrupted"},
            **_empty_sections(note="Intelligence cache corrupted — run python -m bot.daily"),
        }


def sections_for_report() -> dict[str, Any]:
    cache = load_intelligence_cache()
    available = is_cache_available(cache)
    sections = {key: cache.get(key, _empty_sections()[key]) for key in INTELLIGENCE_KEYS}
    sections["intelligence_cache_available"] = available
    sections["intelligence_cache_meta"] = cache.get("meta", {})
    if not available:
        sections["intelligence_cache_note"] = CACHE_UNAVAILABLE_MSG
    return sections
