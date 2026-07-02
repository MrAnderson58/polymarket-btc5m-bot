"""Confidence Engine — rule-based trust score for agent decisions."""

from __future__ import annotations

from typing import Any


def compute_confidence(
    *,
    ai_score: float,
    similar: dict[str, Any],
    walk_forward_ok: bool = True,
) -> float:
    """
    Rule-based confidence 0–100 from sample size, PF, WR, score, stability proxy.
    """
    n = int(similar.get("similar_count", 0))
    pf = similar.get("profit_factor", 0)
    if pf == float("inf"):
        pf = 3.0
    wr = float(similar.get("win_rate", 0))

    conf = 25.0
    conf += min(35.0, n / 8.0)
    if pf >= 2.5:
        conf += 18
    elif pf >= 1.8:
        conf += 12
    elif pf >= 1.2:
        conf += 6
    elif pf < 0.9 and n >= 10:
        conf -= 12

    if wr >= 0.6:
        conf += 10
    elif wr >= 0.5:
        conf += 4
    elif wr < 0.4 and n >= 10:
        conf -= 8

    conf += (ai_score - 50) / 4.0

    if not walk_forward_ok:
        conf -= 10

    avg_dist = similar.get("avg_distance", 0)
    if avg_dist > 5 and n >= 20:
        conf -= 5

    return max(0.0, min(100.0, round(conf, 1)))
