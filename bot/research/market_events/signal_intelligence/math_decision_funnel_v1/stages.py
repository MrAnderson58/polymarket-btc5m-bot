"""Funnel stage definitions + pass checks (research-only, observe thresholds)."""

from __future__ import annotations

from typing import Any, Callable

# Mirror existing paper-math / decision floors — do NOT change Gate/Strategy.
MATCH_FLOOR = 0.50
MIN_FINGERPRINT = 0.30
MIN_TIMELINE = 0.60
MIN_REALITY = 80.0
MIN_RANK = frozenset({"A+", "A", "ELITE"})

FUNNEL_STAGES: tuple[str, ...] = (
    "Candidate",
    "Replay",
    "Fingerprint",
    "Timeline",
    "DNA",
    "Rules",
    "Edge",
    "Causality",
    "Brain",
    "Reality",
    "Elite",
    "Decision",
    "Book B",
    "Book D",
)

# Module stages that can reject (exclude Candidate which is always pass)
MODULE_STAGES: tuple[str, ...] = tuple(s for s in FUNNEL_STAGES if s != "Candidate")


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _is_match(score: Any, *, rules: bool = False) -> bool:
    if rules:
        try:
            return int(score or 0) >= 1
        except Exception:
            return False
    x = _f(score)
    if x is None:
        return False
    return x >= MATCH_FLOOR


def _normalize_rank(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s == "ELITE":
        return "A+"
    return s


def stage_passes(stage: str, row: dict[str, Any], *, ctx: dict[str, Any]) -> bool:
    """Whether trade passes a funnel stage given row + context flags."""
    if stage == "Candidate":
        return True
    if stage == "Replay":
        return _is_match(row.get("replay"))
    if stage == "Fingerprint":
        x = _f(row.get("fingerprint_similarity"))
        return x is not None and x >= MIN_FINGERPRINT
    if stage == "Timeline":
        x = _f(row.get("timeline_similarity"))
        return x is not None and x >= MIN_TIMELINE
    if stage == "DNA":
        return _is_match(row.get("dna"))
    if stage == "Rules":
        return _is_match(row.get("rules"), rules=True)
    if stage == "Edge":
        return _is_match(row.get("edge"))
    if stage == "Causality":
        return _is_match(row.get("causality"))
    if stage == "Brain":
        b = row.get("brain")
        if b is None:
            b = row.get("brain_score")
        return _is_match(b)
    if stage == "Reality":
        rs = ctx.get("reality_score")
        if rs is None:
            rs = row.get("reality_score")
        x = _f(rs)
        return x is not None and x >= MIN_REALITY
    if stage == "Elite":
        rank = _normalize_rank(row.get("decision_rank") or row.get("elite_category") or row.get("category"))
        if rank in MIN_RANK or rank == "A+":
            return True
        # elite_ids set from store
        tid = int(row.get("trade_id") or 0)
        return tid in (ctx.get("elite_ids") or set())
    if stage == "Decision":
        return str(row.get("decision") or "") == "TRADE"
    if stage == "Book B":
        tid = int(row.get("trade_id") or 0)
        return tid in (ctx.get("book_b_ids") or set())
    if stage == "Book D":
        tid = int(row.get("trade_id") or 0)
        return tid in (ctx.get("book_d_ids") or set())
    return False


def rejecting_modules(row: dict[str, Any], *, ctx: dict[str, Any]) -> list[str]:
    """All module stages that reject this trade (ordered by funnel)."""
    out: list[str] = []
    for stage in MODULE_STAGES:
        if not stage_passes(stage, row, ctx=ctx):
            out.append(stage)
    return out


def first_rejector(row: dict[str, Any], *, ctx: dict[str, Any]) -> str | None:
    mods = rejecting_modules(row, ctx=ctx)
    return mods[0] if mods else None


__all__ = [
    "FUNNEL_STAGES",
    "MATCH_FLOOR",
    "MIN_FINGERPRINT",
    "MIN_RANK",
    "MIN_REALITY",
    "MIN_TIMELINE",
    "MODULE_STAGES",
    "first_rejector",
    "rejecting_modules",
    "stage_passes",
]
