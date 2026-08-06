"""Replay reject classification — research-only observation of MATCH_FLOOR."""

from __future__ import annotations

from typing import Any

# Mirror funnel / paper-math Replay floor — do NOT change Decision/Replay engine.
REPLAY_FLOOR = 0.50


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def replay_score(row: dict[str, Any]) -> float | None:
    return _f(row.get("replay"))


def replay_rejects(row: dict[str, Any]) -> bool:
    x = replay_score(row)
    if x is None:
        return True
    return x < REPLAY_FLOOR


def reject_reason(row: dict[str, Any]) -> str:
    x = replay_score(row)
    if x is None:
        return "replay_missing"
    if x < 0.10:
        return "replay_lt_0.10"
    if x < 0.25:
        return "replay_lt_0.25"
    if x < 0.40:
        return "replay_lt_0.40"
    if x < REPLAY_FLOOR:
        return "replay_lt_0.50"
    return "replay_pass"


def pnl_of(row: dict[str, Any]) -> float | None:
    v = row.get("pnl")
    if v is None:
        v = row.get("pnl_usd")
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def outcome_bucket(row: dict[str, Any]) -> str:
    p = pnl_of(row)
    if p is None:
        return "unknown"
    if p > 0:
        return "winner"
    if p < 0:
        return "loser"
    return "flat"


__all__ = [
    "REPLAY_FLOOR",
    "outcome_bucket",
    "pnl_of",
    "reject_reason",
    "replay_rejects",
    "replay_score",
]
