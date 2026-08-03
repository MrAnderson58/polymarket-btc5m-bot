"""WIN/LOSS feature distributions + trade performance metrics."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
    VECTOR_KEYS,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def feature_distributions(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """mean/median/variance per feature for a result bucket."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[str(r.get("result") or "BE")].append(r)

    out: dict[str, dict[str, Any]] = {}
    for label, items in buckets.items():
        stats: dict[str, Any] = {"n": len(items)}
        for k in VECTOR_KEYS:
            xs = [float(it["features"][k]) for it in items if it.get("features", {}).get(k) is not None]
            if not xs:
                stats[k] = {"mean": None, "median": None, "variance": None, "n": 0}
                continue
            arr = np.array(xs, dtype=float)
            stats[k] = {
                "mean": round(float(np.mean(arr)), 6),
                "median": round(float(np.median(arr)), 6),
                "variance": round(float(np.var(arr)), 6),
                "n": len(xs),
            }
        out[label] = stats
    return out


def cluster_performance(pnls: Sequence[float], *, mae: Sequence[float] | None = None,
                        mfe: Sequence[float] | None = None,
                        hold: Sequence[float] | None = None) -> dict[str, Any]:
    met = trade_metrics(pnls)
    xs = [float(p) for p in pnls]
    n = len(xs)
    # Sharpe (simple, pnl series)
    sharpe = None
    if n >= 5:
        mu = float(np.mean(xs))
        sd = float(np.std(xs))
        sharpe = round(mu / sd, 4) if sd > 1e-12 else None
    # MaxDD on cumulative pnl
    max_dd = 0.0
    if n:
        cum = np.cumsum(xs)
        peak = cum[0]
        for v in cum:
            peak = max(peak, float(v))
            max_dd = min(max_dd, float(v) - peak)
    # bootstrap CI on EV
    ci_lo = ci_hi = None
    if n >= 20:
        rng = np.random.default_rng(7)
        boots = []
        idx = np.arange(n)
        for _ in range(200):
            sample = rng.choice(idx, size=n, replace=True)
            boots.append(float(np.mean(np.array(xs)[sample])))
        ci_lo = round(float(np.percentile(boots, 2.5)), 4)
        ci_hi = round(float(np.percentile(boots, 97.5)), 4)
    return {
        **met,
        "sharpe": sharpe,
        "max_dd": round(max_dd, 4),
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "avg_mae": round(float(np.mean(mae)), 4) if mae else None,
        "avg_mfe": round(float(np.mean(mfe)), 4) if mfe else None,
        "avg_hold": round(float(np.mean(hold)), 1) if hold else None,
    }


def mae_mfe_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mae = [float(r["mae"]) for r in rows if r.get("mae") is not None]
    mfe = [float(r["mfe"]) for r in rows if r.get("mfe") is not None]
    return {
        "n_mae": len(mae),
        "n_mfe": len(mfe),
        "avg_mae": round(float(np.mean(mae)), 4) if mae else None,
        "avg_mfe": round(float(np.mean(mfe)), 4) if mfe else None,
        "median_mae": round(float(np.median(mae)), 4) if mae else None,
        "median_mfe": round(float(np.median(mfe)), 4) if mfe else None,
    }


__all__ = ["cluster_performance", "feature_distributions", "mae_mfe_summary"]
