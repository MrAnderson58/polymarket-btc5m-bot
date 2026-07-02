"""Persist optimizer output for read-only Report consumption."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR

CACHE_DIR = BASE_DIR / "optimizer_cache"
RESULTS_PATH = CACHE_DIR / "optimizer_results.json"

CACHE_UNAVAILABLE_MSG = (
    "Optimizer cache unavailable.\n\nRun:\n\npython -m bot.daily"
)


def _default_payload(*, source: str = "empty", note: str | None = None) -> dict[str, Any]:
    return {
        "meta": {
            "generated_at": None,
            "source": source,
            "note": note or "Run python -m bot.optimizer or python -m bot.daily",
        },
        "parameter_optimizer": {
            "current": {},
            "optimal": {},
            "expected_improvement_pct": 0,
            "entry_significance": [],
        },
        "walk_forward": {"rows": [], "trend": "insufficient_data"},
        "heatmaps": {},
        "sensitivity_analysis": {"parameters": []},
        "parameter_stability": {"parameters": []},
        "overfit_detector": {
            "level": "LOW",
            "overfit": False,
            "reasons": ["Optimizer cache unavailable"],
            "recommendation": "Run python -m bot.daily to refresh optimizer cache",
        },
    }


def is_cache_available(cache: dict[str, Any]) -> bool:
    meta = cache.get("meta", {})
    source = meta.get("source", "empty")
    if source in ("empty", "corrupted"):
        return False
    if meta.get("generated_at") or meta.get("cached_at"):
        return True
    return bool(cache.get("parameter_optimizer", {}).get("current"))


def _walk_forward_trend(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "insufficient_data"
    if "generalizes" in rows[0]:
        ok = sum(1 for row in rows if row.get("generalizes"))
        if ok == len(rows):
            return "stable"
        if ok == 0:
            return "degrading"
        return "mixed"
    if len(rows) >= 3 and rows[0].get("avg_pnl") is not None:
        recent = rows[1]
        older = rows[-1]
        if recent.get("avg_pnl", 0) > older.get("avg_pnl", 0) + 1.0:
            return "improving"
        if recent.get("avg_pnl", 0) < older.get("avg_pnl", 0) - 1.0:
            return "degrading"
        return "stable"
    return "stable"


def normalize_walk_forward(wf: Any) -> dict[str, Any]:
    if isinstance(wf, dict) and "rows" in wf:
        rows = wf.get("rows", [])
        trend = wf.get("trend") or _walk_forward_trend(rows)
        return {"rows": rows, "trend": trend}
    if isinstance(wf, list):
        return {"rows": wf, "trend": _walk_forward_trend(wf)}
    return {"rows": [], "trend": "insufficient_data"}


def save_optimizer_cache(report: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            **report.get("meta", {}),
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "cache_version": "1.1",
            "source": "optimizer",
        },
        "parameter_optimizer": report.get("parameter_optimizer", {}),
        "walk_forward": report.get("walk_forward", []),
        "heatmaps": report.get("heatmaps", {}),
        "sensitivity_analysis": report.get("sensitivity_analysis", {}),
        "parameter_stability": report.get("parameter_stability", {"parameters": []}),
        "overfit_detector": report.get("overfit_detector", {}),
        "machine_learning": report.get("machine_learning", {}),
        "recommendations": report.get("recommendations", []),
        "rules_discovery": report.get("rules_discovery", []),
        "cluster_analysis": report.get("cluster_analysis", {}),
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return RESULTS_PATH


def load_optimizer_cache() -> dict[str, Any]:
    if not RESULTS_PATH.exists():
        return _default_payload()
    try:
        return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_payload(
            source="corrupted",
            note="Optimizer cache file is corrupted. Run python -m bot.daily",
        )


def sections_for_report() -> dict[str, Any]:
    """Sections consumed by bot.report (read-only)."""
    cache = load_optimizer_cache()
    available = is_cache_available(cache)
    walk_forward = normalize_walk_forward(cache.get("walk_forward", []))

    sections: dict[str, Any] = {
        "parameter_optimizer": cache.get("parameter_optimizer", {}),
        "walk_forward": walk_forward,
        "heatmaps": cache.get("heatmaps", {}),
        "sensitivity_analysis": cache.get("sensitivity_analysis", {}),
        "parameter_stability": cache.get("parameter_stability", {"parameters": []}),
        "overfit_detector": cache.get("overfit_detector", {}),
        "optimizer_cache_meta": cache.get("meta", {}),
        "optimizer_cache_available": available,
    }
    if not available:
        sections["optimizer_cache_note"] = CACHE_UNAVAILABLE_MSG
    return sections
