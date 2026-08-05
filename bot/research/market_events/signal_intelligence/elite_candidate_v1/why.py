"""WHY / WHY NOT + supporting/rejecting module lists."""

from __future__ import annotations

from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.elite_candidate_v1.score import WEIGHTS


def supporting_and_rejecting(
    components: dict[str, float],
    *,
    pass_threshold: float = 0.55,
    top_n: int = 5,
) -> tuple[list[str], list[str]]:
    ranked = sorted(
        ((m, float(components.get(m) or 0.0)) for m in WEIGHTS),
        key=lambda x: x[1],
        reverse=True,
    )
    supporting = [m for m, v in ranked if v >= pass_threshold][:top_n]
    rejecting = [m for m, v in sorted(ranked, key=lambda x: x[1]) if v < pass_threshold][:top_n]
    return supporting, rejecting


def build_why(
    *,
    components: dict[str, float],
    supporting: Sequence[str],
    category: str,
    decision: str | None,
    direction: str | None,
) -> list[str]:
    lines: list[str] = []
    if category in ("ELITE", "A+", "A"):
        lines.append(f"Candidate {category} via research stack")
    if decision:
        lines.append(f"Decision={decision}")
    if direction:
        lines.append(f"Direction={direction}")
    for m in supporting:
        lines.append(f"{m.title()} PASS ({components.get(m, 0):.2f})")
    if supporting and all(float(components.get(m) or 0) >= 0.55 for m in ("decision", "brain", "replay", "fingerprint", "timeline")):
        lines.append("Replay Fingerprint Timeline Decision Brain ALL PASS")
    return lines[:12]


def build_why_not(
    *,
    components: dict[str, float],
    rejecting: Sequence[str],
    del_strict_modules: Sequence[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    for m in rejecting:
        lines.append(f"{m.title()} weak ({components.get(m, 0):.2f})")
    for m in del_strict_modules or []:
        if m in components and float(components.get(m) or 0) < 0.7:
            lines.append(f"Error-learning: {m} historically too strict")
    return lines[:12]


__all__ = [
    "build_why",
    "build_why_not",
    "supporting_and_rejecting",
]
