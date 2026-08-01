"""Edge decay, slope, and half-life estimation."""

from __future__ import annotations

from typing import Any

import numpy as np


def _bucket_ev(timestamps: np.ndarray, pnls: np.ndarray, *, now: float, days: float) -> float | None:
    lo = now - days * 86400.0
    hi = now - max(0.0, days - 30.0) * 86400.0 if days >= 30 else now
    # For "edge today": last 30d; for 30d ago: [30,60); for 90d ago: [90,120)
    if days <= 0:
        mask = timestamps >= (now - 30 * 86400.0)
    elif days == 30:
        mask = (timestamps >= now - 60 * 86400.0) & (timestamps < now - 30 * 86400.0)
    elif days == 90:
        mask = (timestamps >= now - 120 * 86400.0) & (timestamps < now - 90 * 86400.0)
    else:
        mask = (timestamps >= lo) & (timestamps < hi)
    vals = pnls[mask]
    if vals.size < 3:
        return None
    return round(float(vals.mean()), 6)


def edge_decay_profile(
    timestamps: list[float] | np.ndarray,
    pnls: list[float] | np.ndarray,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    ts = np.asarray(timestamps, dtype=float)
    ys = np.asarray(pnls, dtype=float)
    if ts.size == 0:
        return {
            "edge_today": None, "edge_30d_ago": None, "edge_90d_ago": None,
            "slope": None, "half_life_days": None,
        }
    now = float(now if now is not None else ts.max())
    e0 = _bucket_ev(ts, ys, now=now, days=0)
    e30 = _bucket_ev(ts, ys, now=now, days=30)
    e90 = _bucket_ev(ts, ys, now=now, days=90)

    # Slope from chronological rolling means (10 buckets)
    order = np.argsort(ts)
    ys_o = ys[order]
    n = ys_o.size
    slope = None
    half_life = None
    if n >= 10:
        buckets = 10
        edges = np.array_split(ys_o, buckets)
        means = np.array([float(b.mean()) if b.size else np.nan for b in edges], dtype=float)
        x = np.arange(buckets, dtype=float)
        mask = np.isfinite(means)
        if mask.sum() >= 3:
            coef = np.polyfit(x[mask], means[mask], 1)
            slope = round(float(coef[0]), 6)
            # Half-life via log decay of positive envelope of rolling EV
            # Fit EV(t) ≈ a * exp(-λ t) on positive means, else use linear crossing
            pos = means[mask]
            if np.all(pos <= 0):
                half_life = 0.0
            else:
                # Use absolute trend: days for EV to halve from first to last positive
                first = float(pos[0])
                last = float(pos[-1])
                span_days = max(1.0, (float(ts[order][-1]) - float(ts[order][0])) / 86400.0)
                if first > 1e-9 and last > 1e-9 and last < first:
                    lam = -np.log(last / first) / span_days
                    half_life = round(float(np.log(2) / lam), 2) if lam > 1e-9 else None
                elif first > 1e-9 and last <= 0:
                    # Extrapolate zero-cross as proxy for half of first
                    frac = 0.5
                    # linear interp index where mean hits first/2
                    target = first * frac
                    crossed = None
                    for i in range(1, len(pos)):
                        if pos[i] <= target:
                            crossed = i / max(1, len(pos) - 1) * span_days
                            break
                    half_life = round(float(crossed), 2) if crossed is not None else round(span_days * 0.5, 2)
                elif slope is not None and slope < 0 and first > 0:
                    # days to lose half via linear slope scaled to span
                    per_day = abs(slope) * (buckets / span_days)
                    half_life = round(float((first * 0.5) / per_day), 2) if per_day > 1e-12 else None
                else:
                    half_life = None

    return {
        "edge_today": e0,
        "edge_30d_ago": e30,
        "edge_90d_ago": e90,
        "slope": slope,
        "half_life_days": half_life,
    }


__all__ = ["edge_decay_profile"]
