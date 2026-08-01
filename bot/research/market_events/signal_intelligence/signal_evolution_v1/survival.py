"""Simple survival analysis for signal profitability persistence."""

from __future__ import annotations

from typing import Any

import numpy as np


def survival_probability(
    timestamps: list[float] | np.ndarray,
    pnls: list[float] | np.ndarray,
    *,
    window: int = 50,
    horizon_days: float = 30.0,
) -> dict[str, Any]:
    """
    Estimate P(signal remains profitable).

    Death event: trailing `window` EV crosses from >0 to ≤0.
    Survival curve via discrete Kaplan–Meier on those events.
    """
    ts = np.asarray(timestamps, dtype=float)
    ys = np.asarray(pnls, dtype=float)
    if ts.size < window + 5:
        wr = float((ys > 0).mean()) if ys.size else 0.0
        return {
            "survival_prob": round(max(0.0, min(1.0, wr)), 4),
            "n_deaths": 0,
            "n_at_risk": int(ys.size),
            "method": "prior_wr",
        }
    order = np.argsort(ts)
    ys = ys[order]
    ts = ts[order]
    # Trailing EV series
    csum = np.cumsum(ys)
    trail = []
    death_times = []
    alive = True
    for i in range(window - 1, ys.size):
        prev = csum[i - window] if i - window >= 0 else 0.0
        ev = (csum[i] - prev) / window
        trail.append(ev)
        if alive and ev <= 0 and i >= window:
            # confirm previous was positive
            if len(trail) >= 2 and trail[-2] > 0:
                death_times.append(float(ts[i]))
                alive = False
        if not alive and ev > 0:
            alive = True  # rebirth allowed for KM risk set refresh

    n_deaths = len(death_times)
    n_at_risk = max(1, ys.size - window + 1)
    # KM: S = Π (1 - d_i / n_i); approximate with exponential hazard
    hazard = n_deaths / n_at_risk
    # P(survive horizon): exp(-hazard * horizon_scale)
    age_days = max(1.0, (float(ts[-1]) - float(ts[0])) / 86400.0)
    rate_per_day = hazard / age_days
    surv = float(np.exp(-rate_per_day * horizon_days))
    # Blend with current trailing EV sign
    current_ev = float(trail[-1]) if trail else 0.0
    if current_ev > 0:
        surv = min(1.0, surv + 0.1)
    else:
        surv = max(0.0, surv * 0.5)
    return {
        "survival_prob": round(max(0.0, min(1.0, surv)), 4),
        "n_deaths": n_deaths,
        "n_at_risk": n_at_risk,
        "current_trailing_ev": round(current_ev, 6),
        "horizon_days": horizon_days,
        "method": "kaplan_meier_hazard",
    }


__all__ = ["survival_probability"]
