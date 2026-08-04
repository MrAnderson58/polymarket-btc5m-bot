"""Top-100 false reject attribution — which single threshold caused rejection."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.evaluate import (
    accept_mask,
    binding_threshold,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.grid import (
    ThresholdSet,
)


def analyze_top_false_rejects(
    cache: dict[str, Any],
    thr: ThresholdSet,
    *,
    top_n: int = 100,
) -> dict[str, Any]:
    """
    False reject = profitable trade rejected by thr.
    Attribute each to a single binding threshold; estimate EV recovered if relaxed.
    """
    mask = accept_mask(cache, thr)
    pnl = cache["pnl"]
    rejected_profitable = np.where((~mask) & (pnl > 0))[0]
    if len(rejected_profitable) == 0:
        return {
            "n": 0,
            "top": [],
            "binding_counts": {},
            "ev_recovered_if_relaxed": {},
            "total_pnl_left_on_table": 0.0,
        }

    # sort by pnl desc
    order = rejected_profitable[np.argsort(-pnl[rejected_profitable])]
    top_idx = order[:top_n]

    top_rows: list[dict[str, Any]] = []
    binding_counts: Counter[str] = Counter()
    pnl_by_binding: dict[str, float] = {}

    for i in top_idx:
        ii = int(i)
        bind = binding_threshold(cache, ii, thr) or "unknown"
        binding_counts[bind] += 1
        p = float(pnl[ii])
        pnl_by_binding[bind] = pnl_by_binding.get(bind, 0.0) + p
        top_rows.append({
            "trade_id": int(cache["trade_ids"][ii]),
            "symbol": cache["symbols"][ii],
            "pnl": p,
            "binding_threshold": bind,
            "fp_sim": float(cache["fp_sim"][ii]),
            "tl_sim": float(cache["tl_sim"][ii]),
            "conf": float(cache["conf"][ii]),
            "hist_wr": float(cache["hist_wr"][ii]),
            "hist_pf": float(cache["hist_pf"][ii]),
            "rules_n": int(cache["rules_n"][ii]),
            "why": (cache.get("why_baseline") or [[]])[ii][:4],
        })

    # EV recovered if each binding threshold relaxed enough to accept these top rejects
    n_all = max(int(cache["n"] or 1), 1)
    ev_recovered = {
        k: round(v / n_all, 6)  # crude portfolio EV contribution
        for k, v in pnl_by_binding.items()
    }
    # Also report mean EV of those rejected trades (per-trade EV if accepted)
    ev_per_trade = {
        k: round(pnl_by_binding[k] / max(binding_counts[k], 1), 4)
        for k in binding_counts
    }

    return {
        "n": len(rejected_profitable),
        "top_n": len(top_rows),
        "top": top_rows,
        "binding_counts": dict(binding_counts.most_common()),
        "pnl_by_binding": {k: round(v, 4) for k, v in pnl_by_binding.items()},
        "ev_recovered_if_relaxed": ev_recovered,
        "mean_pnl_if_relaxed": ev_per_trade,
        "total_pnl_left_on_table": round(float(np.sum(pnl[order])), 4),
        "top100_pnl": round(float(np.sum(pnl[top_idx])), 4),
    }


__all__ = ["analyze_top_false_rejects"]
