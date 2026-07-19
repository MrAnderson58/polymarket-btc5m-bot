"""S43 — event confidence + freshness scoring."""

from __future__ import annotations

from typing import Iterable

from bot.research.market_events.signal_intelligence.event_intelligence.reputation import (
    source_reputation,
)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def freshness_score(last_seen: int, *, now: int, half_life_sec: int = 6 * 3600) -> float:
    """Exponential-ish decay: newest events dominate."""
    age = max(0, int(now) - int(last_seen))
    if age <= 600:
        return 1.0
    # Linear-ish decay to ~0 over ~4 half-lives
    return _clamp(1.0 - (age / float(half_life_sec * 4)))


def agreement_score(sentiments: Iterable[float]) -> float:
    vals = [float(s) for s in sentiments]
    if not vals:
        return 0.0
    if len(vals) == 1:
        return 0.55
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    # Low variance → high agreement
    return _clamp(1.0 - (var * 4.0))


def event_confidence(
    *,
    sources: list[str],
    headline_count: int,
    agreement: float,
    freshness: float,
    importance: float,
) -> float:
    reps = [source_reputation(s) for s in sources] or [0.4]
    avg_rep = sum(reps) / len(reps)
    # Distinct high-quality sources boost strongly
    uniq = len({(s or "Unknown").strip().lower() for s in sources if s})
    source_div = _clamp(uniq / 5.0)
    volume = _clamp(headline_count / 6.0)
    score = (
        0.28 * avg_rep
        + 0.22 * source_div
        + 0.18 * volume
        + 0.16 * _clamp(agreement)
        + 0.10 * _clamp(freshness)
        + 0.06 * _clamp(importance)
    )
    return round(_clamp(score), 3)
