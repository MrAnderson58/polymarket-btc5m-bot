"""Directed causal graph with edge weights."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_causality_v1.features import (
    CAUSAL_TEMPLATE_EDGES,
    CAUSE_FEATURES,
)


def _series(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    return np.asarray([float((r.get("cause_vec") or {}).get(key) or 0.0) for r in rows], dtype=float)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 5:
        return 0.0
    if float(np.std(a)) < 1e-12 or float(np.std(b)) < 1e-12:
        return 0.0
    c = float(np.corrcoef(a, b)[0, 1])
    return c if np.isfinite(c) else 0.0


def build_causal_graph(
    rows: list[dict[str, Any]],
    *,
    outcomes: np.ndarray | None = None,
) -> dict[str, Any]:
    """
    Build directed weighted graph from template + observed co-movements.

    Edge weight = max(0, corr(parent, child)) * support.
    Only features known pre-entry (cause_vec) may point toward outcome.
    """
    n = len(rows)
    if outcomes is None:
        outcomes = np.asarray([float(r.get("pnl") or 0.0) for r in rows], dtype=float)
    win = (outcomes > 0).astype(float)

    nodes = set(CAUSE_FEATURES) | {"breakout", "trend", "mean_reversion", "outcome"}
    edges: list[dict[str, Any]] = []
    weight_sum = 0.0

    # Derived mediators from cause vectors
    for r in rows:
        cv = r.get("cause_vec") or {}
        r["_breakout"] = float(cv.get("atr") or 0) + float(cv.get("volume") or 0) + float(cv.get("pattern") or 0)
        r["_trend"] = float(cv.get("ema") or 0) + float(cv.get("macd") or 0) + float(cv.get("adx") or 0)
        r["_mean_reversion"] = float(cv.get("rsi") or 0)

    series_cache: dict[str, np.ndarray] = {}
    for feat in CAUSE_FEATURES:
        series_cache[feat] = _series(rows, feat)
    series_cache["breakout"] = np.asarray([float(r.get("_breakout") or 0) for r in rows])
    series_cache["trend"] = np.asarray([float(r.get("_trend") or 0) for r in rows])
    series_cache["mean_reversion"] = np.asarray([float(r.get("_mean_reversion") or 0) for r in rows])
    series_cache["outcome"] = win

    for src, dst in CAUSAL_TEMPLATE_EDGES:
        a = series_cache.get(src)
        b = series_cache.get(dst)
        if a is None or b is None:
            continue
        w = abs(_corr(a, b))
        support = float(np.mean((a > 0) & (b > 0))) if n else 0.0
        # Co-activation fallback when series are nearly constant (corr≈0).
        act = float(np.mean(a > 0)) * float(np.mean(b > 0))
        weight = round(max(w * (0.5 + 0.5 * support), 0.08 * support, 0.05 * act), 6)
        if weight <= 1e-6:
            continue
        edges.append({
            "source": src,
            "target": dst,
            "weight": weight,
            "corr": round(float(_corr(a, b)), 6),
            "support": round(support, 6),
        })
        weight_sum += weight

    # Rank edges
    edges.sort(key=lambda e: -float(e["weight"]))
    return {
        "nodes": sorted(nodes),
        "edges": edges,
        "n_edges": len(edges),
        "n_nodes": len(nodes),
        "total_weight": round(weight_sum, 6),
        "top_edges": edges[:20],
    }


def graph_markdown(graph: dict[str, Any]) -> list[str]:
    lines = ["```", "Causal graph (top edges)", ""]
    for e in (graph.get("top_edges") or [])[:12]:
        lines.append(f"{e['source']} --({e['weight']})--> {e['target']}")
    lines.append("```")
    return lines


__all__ = ["build_causal_graph", "graph_markdown"]
