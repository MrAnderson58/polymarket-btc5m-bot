"""Confidence scoring for asset intelligence records."""

from __future__ import annotations

import time
from typing import Any, Iterable


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def calculate_confidence(
    *,
    source_qualities: Iterable[float],
    headline_count: int,
    importance: float,
    agreement: float,
    freshness_sec: float | None,
    now: int | None = None,
) -> float:
    """Combine source quality, volume, importance, agreement, freshness → 0..1."""
    quals = [float(q) for q in source_qualities]
    avg_quality = sum(quals) / len(quals) if quals else 0.4
    volume = _clamp(headline_count / 8.0)
    imp = _clamp(float(importance))
    agree = _clamp(float(agreement))

    fresh = 0.5
    if freshness_sec is not None:
        # Full credit under 1h, decay to ~0 after 24h
        fresh = _clamp(1.0 - (float(freshness_sec) / 86400.0))

    score = (
        0.30 * avg_quality
        + 0.20 * volume
        + 0.20 * imp
        + 0.20 * agree
        + 0.10 * fresh
    )
    _ = now  # reserved for future clock skew handling
    return round(_clamp(score), 3)


def agreement_from_sentiments(items: list[dict[str, Any]]) -> float:
    """High when bullish/bearish lean is consistent across headlines."""
    if not items:
        return 0.0
    if len(items) == 1:
        return 0.55
    lean = 0
    for it in items:
        b = float(it.get("bullish") or 0)
        s = float(it.get("bearish") or 0)
        if b > s + 0.05:
            lean += 1
        elif s > b + 0.05:
            lean -= 1
    return round(_clamp(abs(lean) / len(items)), 3)


def freshness_sec_from_timestamps(
    timestamps: Iterable[int],
    *,
    now: int | None = None,
) -> float | None:
    ts = [int(t) for t in timestamps if t]
    if not ts:
        return None
    now_ts = int(now if now is not None else time.time())
    return float(max(0, now_ts - max(ts)))
