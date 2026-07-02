"""Evolution status types and optional shadow phase (Phase 1: read-only, no shadow)."""

from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict


class EvolutionStatus(str, Enum):
    KEEP = "KEEP"
    WATCH = "WATCH"
    READY_FOR_SHADOW = "READY_FOR_SHADOW"
    SHADOW_RUNNING = "SHADOW_RUNNING"
    SHADOW_PROMOTE = "SHADOW_PROMOTE"
    SHADOW_REJECT = "SHADOW_REJECT"


EVOLUTION_VERSION = "1.0"


class EvolutionCandidate(TypedDict, total=False):
    parameter: str
    from_value: str | float
    to_value: str | float
    label: str
    confidence_pct: float
    evidence_trades: int
    expected_pf_pct: float
    expected_dd_pct: float | None
    walk_forward: str
    overfit: str


class EvolutionResult(TypedDict, total=False):
    version: str
    status: str
    reason: str
    next_review_trades: int | None
    candidate: EvolutionCandidate | None
    evidence: dict[str, Any]
    watch_reasons: list[str]
    sources_meta: dict[str, Any]


def load_shadow_phase_state() -> dict[str, Any] | None:
    """
    Phase 1: shadow experiments are not created yet.
    Reserved for Phase 2 — returns None (no file reads).
    """
    return None
