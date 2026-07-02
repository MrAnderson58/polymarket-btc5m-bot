"""Persist Strategy Review for read-only Report consumption."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR
from bot.strategy_review.constants import REVIEW_VERSION, SAFETY_GATE

CACHE_DIR = BASE_DIR / "strategy_review_cache"
RESULTS_PATH = CACHE_DIR / "strategy_review_results.json"


def _default_payload() -> dict[str, Any]:
    return {
        "version": REVIEW_VERSION,
        "mode": "observe_only",
        "disclaimer": "Run python -m bot.daily to compute Strategy Review.",
        "safety_gate": SAFETY_GATE,
        "final_verdict": {
            "decision": "KEEP CURRENT SETTINGS",
            "confidence_pct": 0,
            "reason": "Strategy Review cache missing — run daily pipeline.",
            "safety_blocked": True,
        },
    }


def save_strategy_review_cache(review: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        **review,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return RESULTS_PATH


def load_strategy_review_cache() -> dict[str, Any]:
    if not RESULTS_PATH.exists():
        return _default_payload()
    try:
        return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default_payload()
