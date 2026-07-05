"""Shared metrics for HTF research pipelines."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

BOOTSTRAP_SAMPLES = 1000
STRESS_LEVELS = (0.01, 0.02)


def pf(pnls: list[float]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    return wins / losses if losses else (wins if wins > 0 else 0.0)


def max_drawdown(pnls: list[float]) -> float:
    eq = peak = 0.0
    mdd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def max_consecutive_losses(pnls: list[float]) -> int:
    cl = mx = 0
    for p in pnls:
        if p < 0:
            cl += 1
            mx = max(mx, cl)
        else:
            cl = 0
    return mx


def apply_stress(trades: list, slip: float) -> list[float]:
    out = []
    for t in trades:
        if t.pnl_pct is None:
            continue
        entry = min(0.99, t.entry_price + slip)
        exit_p = t.exit_price or entry
        out.append((exit_p - entry) / entry * 100.0)
    return out


def bootstrap_p_pf_gt1(pnls: list[float], *, samples: int = BOOTSTRAP_SAMPLES) -> float | None:
    if len(pnls) < 10:
        return None
    rng = random.Random(42)
    hits = n = min(samples, 500)
    count = 0
    for _ in range(n):
        sample = [pnls[rng.randrange(len(pnls))] for _ in range(len(pnls))]
        if pf(sample) > 1.0:
            count += 1
    return count / n


def temporal_halves_stable(pnls: list[float]) -> bool:
    if len(pnls) < 20:
        return False
    mid = len(pnls) // 2
    return pf(pnls[:mid]) > 1.0 and pf(pnls[mid:]) > 1.0


def temporal_thirds(pnls: list[float]) -> dict[str, float]:
    if len(pnls) < 15:
        return {}
    n = len(pnls)
    s = n // 3
    return {
        "t1_pf": pf(pnls[:s]),
        "t2_pf": pf(pnls[s:2 * s]),
        "t3_pf": pf(pnls[2 * s:]),
    }


def regime_slices(trades: list, regime_fn) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        if t.pnl_pct is None:
            continue
        buckets[regime_fn(t)].append(t.pnl_pct)
    return {
        k: {"n": len(v), "pf": round(pf(v), 3) if v else 0}
        for k, v in buckets.items()
    }


def summarize_trades(trades: list) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades if t.pnl_pct is not None]
    if not pnls:
        return {"n": 0}
    wins = [p for p in pnls if p > 0]
    return {
        "n": len(pnls),
        "pf": round(pf(pnls), 3),
        "wr": round(len(wins) / len(pnls) * 100, 1),
        "avg_pnl": round(sum(pnls) / len(pnls), 2),
        "max_dd": round(max_drawdown(pnls), 2),
        "max_cl": max_consecutive_losses(pnls),
        "stress_pp_01": {f"pf_slip_{s}": round(pf(apply_stress(trades, s)), 3) for s in STRESS_LEVELS},
        "bootstrap_p_pf_gt1": bootstrap_p_pf_gt1(pnls),
        "temporal_thirds": temporal_thirds(pnls),
    }

