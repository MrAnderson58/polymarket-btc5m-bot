"""Per-trade contribution attribution (normalized to 100%)."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_causality_v1.features import (
    CAUSE_FEATURES,
)


def normalize_contributions(raw: dict[str, float]) -> dict[str, float]:
    vals = {k: max(0.0, float(raw.get(k) or 0.0)) for k in CAUSE_FEATURES}
    total = sum(vals.values())
    if total <= 1e-12:
        # uniform noise fallback
        u = round(100.0 / len(CAUSE_FEATURES), 4)
        return {k: u for k in CAUSE_FEATURES}
    return {k: round(100.0 * v / total, 4) for k, v in vals.items()}


def attribute_trade(
    cause_vec: dict[str, float],
    *,
    pnl: float,
) -> dict[str, Any]:
    """Score feature contributions; weight slightly by outcome magnitude."""
    mag = abs(float(pnl)) + 1.0
    raw = {
        k: float(np.log1p(max(0.0, float(cause_vec.get(k) or 0.0)))) * mag
        for k in CAUSE_FEATURES
    }
    contrib = normalize_contributions(raw)
    ranked = sorted(contrib.items(), key=lambda t: -t[1])
    primary = ranked[0][0] if ranked else "noise"
    secondary = ranked[1][0] if len(ranked) > 1 else "noise"
    # If top is tiny, mark noise
    if ranked and ranked[0][1] < 100.0 / len(CAUSE_FEATURES) * 1.2:
        if sum(1 for _, v in ranked if v > 0) <= 2:
            primary = "noise"
    return {
        "contributions": contrib,
        "primary_cause": primary,
        "secondary_cause": secondary,
        "top3": [{"feature": k, "pct": v} for k, v in ranked[:3]],
    }


def portfolio_mean_contributions(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {k: 0.0 for k in CAUSE_FEATURES}
    acc = {k: 0.0 for k in CAUSE_FEATURES}
    for r in rows:
        c = r.get("contributions") or {}
        for k in CAUSE_FEATURES:
            acc[k] += float(c.get(k) or 0.0)
    n = len(rows)
    return {k: round(v / n, 4) for k, v in acc.items()}


__all__ = [
    "attribute_trade",
    "normalize_contributions",
    "portfolio_mean_contributions",
]
