"""Scheduler — orchestrate Scientist research cycle."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from bot.scientist.constants import MIN_SAMPLE_TRADES, QUALITY_RULES, SCIENTIST_VERSION
from bot.scientist.experiments import count_by_status, load_experiments, persist_hypothesis, upsert_experiment
from bot.scientist.hypothesis import generate_hypotheses, mark_hypothesis_seen
from bot.scientist.ranking import rank_hypothesis, rank_experiments
from bot.scientist.research import discover_patterns, load_patterns
from bot.scientist.validation import validate_hypothesis


def _best_next_step(experiments: list[dict[str, Any]], total_trades: int) -> dict[str, Any]:
    eligible = [
        e
        for e in experiments
        if e.get("status") == "PASSED"
        and (e.get("validation") or {}).get("eligible_for_recommendation")
    ]
    ranked = rank_experiments(eligible)

    if total_trades < MIN_SAMPLE_TRADES:
        return {
            "recommendation": None,
            "blocked": True,
            "reason": (
                f"INSUFFICIENT DATA — {total_trades} closed trades; "
                f"need {MIN_SAMPLE_TRADES}–{QUALITY_RULES['target_sample']} "
                "before Scientist can recommend experiments."
            ),
            "quality_rules": QUALITY_RULES,
        }

    if not ranked:
        return {
            "recommendation": None,
            "blocked": True,
            "reason": (
                "No experiment passed all quality gates "
                "(sample ≥300, walk-forward, low overfit). "
                "Accumulate more data or wait for stronger patterns."
            ),
            "quality_rules": QUALITY_RULES,
        }

    best = ranked[0]
    validation = best.get("validation", {})
    checks = validation.get("checks", {})
    reasons = []
    if checks.get("replay", {}).get("passed"):
        reasons.append("Passed replay")
    if checks.get("walk_forward", {}).get("passed"):
        reasons.append("Passed walk-forward")
    if checks.get("overfit", {}).get("risk") in ("LOW", "MEDIUM"):
        reasons.append(f"Overfit risk {checks['overfit']['risk']}")

    return {
        "recommendation": best["title"],
        "description": best["description"],
        "expected_pf_pct": round((float(best.get("expected_pf") or 1) - 1) * 100, 1),
        "confidence_pct": round(float(best.get("confidence") or 0), 1),
        "priority": best.get("priority"),
        "risk": best.get("risk_level"),
        "reasons": reasons,
        "blocked": False,
        "experiment_id": best.get("id"),
    }


def run_scientist_cycle(conn: sqlite3.Connection) -> dict[str, Any]:
    """Full observe-only research pass."""
    patterns = discover_patterns(conn)
    new_hypotheses = generate_hypotheses(conn)

    created = 0
    for hypothesis in new_hypotheses:
        validation = validate_hypothesis(conn, hypothesis)
        ranking = rank_hypothesis(hypothesis, validation)
        hid = persist_hypothesis(conn, hypothesis)
        upsert_experiment(
            conn,
            hypothesis_id=hid,
            hypothesis=hypothesis,
            validation=validation,
            ranking=ranking,
        )
        mark_hypothesis_seen(conn, hypothesis)
        created += 1

    all_experiments = load_experiments(conn, limit=200)
    top = rank_experiments(all_experiments)[:10]
    failed = [e for e in all_experiments if e["status"] in ("FAILED", "REJECTED")]
    status_counts = count_by_status(conn)

    total_trades = conn.execute(
        "SELECT COUNT(*) AS n FROM early_reversion_v2_trades WHERE status = 'closed'"
    ).fetchone()["n"]

    summary = {
        "version": SCIENTIST_VERSION,
        "mode": "observe_only",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "patterns_found": len(patterns),
        "new_hypotheses": len(new_hypotheses),
        "experiments_created": created,
        "status_counts": status_counts,
        "total_experiments": sum(status_counts.values()),
        "passed": status_counts.get("PASSED", 0),
        "failed": status_counts.get("FAILED", 0) + status_counts.get("REJECTED", 0),
        "quality_rules": QUALITY_RULES,
    }

    return {
        "summary": summary,
        "new_hypotheses": new_hypotheses,
        "top_experiments": top,
        "failed_ideas": failed[:15],
        "patterns": load_patterns(conn),
        "best_next_step": _best_next_step(all_experiments, int(total_trades)),
    }
