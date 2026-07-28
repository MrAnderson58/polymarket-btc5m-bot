"""Validate / re-score research hypotheses against current research layers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.research.market_events.hypothesis_engine.generate import (
    MIN_SAMPLE_FLAG,
    generate_hypothesis_candidates,
    score_hypothesis,
)
from bot.research.market_events.hypothesis_engine.schema import (
    STATUS_ARCHIVED,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_TESTING,
    STATUS_VALIDATED,
    ensure_hypothesis_engine_schema,
)
from bot.research.market_events.hypothesis_engine.store import (
    list_hypotheses,
    upsert_hypothesis,
)


def decide_status(
    *,
    evidence_score: int,
    confidence: float,
    sample_size: int,
    effect_magnitude: float,
    previous_status: str | None = None,
) -> str:
    """Map scores → NEW / TESTING / VALIDATED / REJECTED (never auto-enable filters)."""
    if previous_status == STATUS_ARCHIVED:
        return STATUS_ARCHIVED
    if evidence_score <= 0:
        return STATUS_REJECTED
    if sample_size < 5:
        return STATUS_REJECTED
    mag = abs(float(effect_magnitude or 0.0))
    # Flat / null effect is not a validated edge — keep as NEW or reject weak cases.
    if mag < 0.01:
        if evidence_score < 2 or sample_size < MIN_SAMPLE_FLAG:
            return STATUS_REJECTED
        return STATUS_NEW
    if (
        evidence_score >= 2
        and confidence >= 70
        and sample_size >= MIN_SAMPLE_FLAG
        and mag >= 0.05
    ):
        return STATUS_VALIDATED
    if evidence_score >= 1 and confidence >= 40:
        return STATUS_TESTING
    return STATUS_NEW


def validate_candidate(candidate: dict[str, Any], *, previous_status: str | None = None) -> dict[str, Any]:
    """Recompute scores and status for one candidate."""
    evidence = list(candidate.get("evidence") or [])
    sample_size = int(candidate.get("sample_size") or 0)
    effect = float(candidate.get("effect_magnitude") or 0.0)
    scores = score_hypothesis(
        evidence=evidence,
        sample_size=sample_size,
        effect_magnitude=effect,
    )
    status = decide_status(
        evidence_score=int(scores["evidence_score"]),
        confidence=float(scores["confidence"]),
        sample_size=sample_size,
        effect_magnitude=effect,
        previous_status=previous_status,
    )
    out = dict(candidate)
    out.update(scores)
    out["status"] = status
    return out


def run_hypothesis_validate(
    conn: Any,
    *,
    patterns_root: Path | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """
    Generate hypotheses from current research layers, re-validate, persist.
    Returns summary stats + persisted bundle metadata.
    """
    ensure_hypothesis_engine_schema(conn)
    existing = {str(h["hypothesis_key"]): h for h in list_hypotheses(conn)}
    candidates = generate_hypothesis_candidates(conn, patterns_root=patterns_root)

    # Archive hypotheses no longer supported by sources
    active_keys = {str(c["hypothesis_key"]) for c in candidates}
    archived = 0
    for key, row in existing.items():
        if key not in active_keys and row.get("status") not in (STATUS_ARCHIVED, STATUS_REJECTED):
            upsert_hypothesis(
                conn,
                hypothesis_key=key,
                title=str(row.get("title") or key),
                description=str(row.get("description") or ""),
                generated_from=str(row.get("generated_from") or ""),
                confidence=float(row.get("confidence") or 0),
                priority=float(row.get("priority") or 0),
                evidence_score=int(row.get("evidence_score") or 0),
                sample_size=int(row.get("sample_size") or 0),
                status=STATUS_ARCHIVED,
                evidence=[],  # cleared — no longer evidenced
            )
            archived += 1

    counts = {
        STATUS_NEW: 0,
        STATUS_TESTING: 0,
        STATUS_VALIDATED: 0,
        STATUS_REJECTED: 0,
        STATUS_ARCHIVED: archived,
    }
    upserted = 0
    for raw in candidates:
        prev = existing.get(str(raw["hypothesis_key"]))
        prev_status = str(prev["status"]) if prev else None
        validated = validate_candidate(raw, previous_status=prev_status)
        upsert_hypothesis(
            conn,
            hypothesis_key=str(validated["hypothesis_key"]),
            title=str(validated["title"]),
            description=str(validated.get("description") or ""),
            generated_from=str(validated["generated_from"]),
            confidence=float(validated["confidence"]),
            priority=float(validated["priority"]),
            evidence_score=int(validated["evidence_score"]),
            sample_size=int(validated["sample_size"]),
            status=str(validated["status"]),
            evidence=list(validated.get("evidence") or []),
        )
        upserted += 1
        st = str(validated["status"])
        counts[st] = counts.get(st, 0) + 1

    if commit:
        try:
            conn.commit()
        except Exception:
            pass

    return {
        "generated": len(candidates),
        "upserted": upserted,
        "archived": archived,
        "counts": counts,
        "low_sample": sum(
            1
            for c in candidates
            if int(c.get("sample_size") or 0) < MIN_SAMPLE_FLAG
        ),
    }
