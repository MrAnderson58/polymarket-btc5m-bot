"""Build Scientist report payload."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.scientist.constants import QUALITY_RULES, SCIENTIST_VERSION
from bot.scientist.experiments import count_by_status, load_experiments
from bot.scientist.research import load_patterns
from bot.scientist.scheduler import run_scientist_cycle


def build_scientist_section(conn: sqlite3.Connection, *, run_cycle: bool = True) -> dict[str, Any]:
    """Run cycle (if requested) and return report section payload."""
    if run_cycle:
        payload = run_scientist_cycle(conn)
        conn.commit()
    else:
        experiments = load_experiments(conn, limit=200)
        payload = {
            "summary": {
                "version": SCIENTIST_VERSION,
                "mode": "observe_only",
                "status_counts": count_by_status(conn),
                "quality_rules": QUALITY_RULES,
            },
            "new_hypotheses": [],
            "top_experiments": experiments[:10],
            "failed_ideas": [e for e in experiments if e["status"] in ("FAILED", "REJECTED")][:15],
            "patterns": load_patterns(conn),
            "best_next_step": {"blocked": True, "reason": "Cycle not run"},
        }

    return {
        "version": SCIENTIST_VERSION,
        "mode": "observe_only",
        "disclaimer": (
            "AI Scientist proposes experiments only. "
            "No automatic config changes. Human approval required."
        ),
        "quality_rules": QUALITY_RULES,
        **payload,
    }
