"""Markov transition matrix over regime states."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.states import (
    STATES,
    canon_state,
)


def build_markov(ordered: list[dict[str, Any]]) -> dict[str, Any]:
    states = [canon_state(r) for r in ordered]
    pnls = [float(r.get("pnl") or r.get("pnl_pct") or 0) for r in ordered]
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    edge_pnls: dict[tuple[str, str], list[float]] = defaultdict(list)
    dwell: dict[str, list[int]] = defaultdict(list)

    # dwell times
    if states:
        run_s = states[0]
        run_len = 1
        for s in states[1:]:
            if s == run_s:
                run_len += 1
            else:
                dwell[run_s].append(run_len)
                run_s = s
                run_len = 1
        dwell[run_s].append(run_len)

    for i in range(len(states) - 1):
        a, b = states[i], states[i + 1]
        counts[a][b] += 1
        edge_pnls[(a, b)].append(pnls[i + 1])

    matrix: dict[str, dict[str, float]] = {}
    edges: list[dict[str, Any]] = []
    for a in STATES:
        total = sum(counts[a].values())
        matrix[a] = {}
        for b in STATES:
            c = int(counts[a].get(b) or 0)
            prob = round(c / total, 4) if total else 0.0
            matrix[a][b] = prob
            if c:
                perf = cluster_performance(edge_pnls[(a, b)])
                dur = dwell.get(a) or []
                edges.append({
                    "from_state": a,
                    "to_state": b,
                    "count": c,
                    "prob": prob,
                    "expected_duration": round(sum(dur) / len(dur), 3) if dur else None,
                    "expected_ev": perf.get("ev"),
                    "expected_wr": perf.get("wr"),
                    "expected_pf": perf.get("pf"),
                })

    current = states[-1] if states else "UNKNOWN"
    nxt = matrix.get(current) or {}
    ranked = sorted(
        ((s, p) for s, p in nxt.items() if p > 0),
        key=lambda x: x[1],
        reverse=True,
    )
    return {
        "states": list(STATES),
        "matrix": matrix,
        "edges": edges,
        "n_transitions": max(0, len(states) - 1),
        "current_state": current,
        "next_probs": [{"state": s, "prob": p} for s, p in ranked],
        "dwell": {k: round(sum(v) / len(v), 3) for k, v in dwell.items() if v},
    }


__all__ = ["build_markov"]
