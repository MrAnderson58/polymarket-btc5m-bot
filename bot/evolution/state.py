"""Evolution status types and shadow phase state."""

from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict

from bot.evolution.constants import EVOLUTION_VERSION


class EvolutionStatus(str, Enum):
    KEEP = "KEEP"
    WATCH = "WATCH"
    READY_FOR_SHADOW = "READY_FOR_SHADOW"
    SHADOW_RUNNING = "SHADOW_RUNNING"
    SHADOW_PROMOTE = "SHADOW_PROMOTE"
    SHADOW_REJECT = "SHADOW_REJECT"


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
    shadow: dict[str, Any]


def load_shadow_phase_state(conn: Any | None = None) -> dict[str, Any] | None:
    if conn is None:
        return None
    from bot.evolution.shadow import shadow_state_for_decision

    return shadow_state_for_decision(conn)
