"""Prior-trade sequence analysis (3/5/10 lookbacks)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)


def sequence_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Study patterns like LOSS×3 → next SHORT WR/PF, WIN×3 → next LONG WR/PF.
    Uses global chronological order (by closed_at).
    """
    ordered = sorted(rows, key=lambda r: int(r.get("closed_at") or r.get("opened_at") or 0))
    results = [str(r.get("result") or "BE") for r in ordered]
    dirs = [str(r.get("direction") or "") for r in ordered]
    pnls = [float(r.get("pnl") or 0) for r in ordered]

    out: dict[str, Any] = {}
    for lookback in (3, 5, 10):
        buckets: dict[str, list[float]] = defaultdict(list)
        for i in range(lookback, len(ordered)):
            pref = tuple(results[i - lookback : i])
            # focus on all-LOSS / all-WIN prefixes
            if all(x == "LOSS" for x in pref):
                key = f"LOSS×{lookback}→{dirs[i] or 'UNK'}"
                buckets[key].append(pnls[i])
            if all(x == "WIN" for x in pref):
                key = f"WIN×{lookback}→{dirs[i] or 'UNK'}"
                buckets[key].append(pnls[i])
        summarized = []
        for key, vals in buckets.items():
            if len(vals) < 20:
                continue
            perf = cluster_performance(vals)
            summarized.append({
                "pattern": key,
                "n": perf["n"],
                "wr": perf["wr"],
                "pf": perf["pf"],
                "ev": perf["ev"],
            })
        summarized.sort(key=lambda x: (float(x.get("pf") or 0), int(x.get("n") or 0)), reverse=True)
        out[str(lookback)] = summarized[:20]
    return out


__all__ = ["sequence_analysis"]
