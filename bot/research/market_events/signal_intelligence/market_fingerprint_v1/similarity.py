"""kNN similarity against historical fingerprint library."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
    VECTOR_KEYS,
    vector_matrix,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)


def build_similarity_index(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fit NearestNeighbors on snapshot vectors."""
    if len(rows) < 10:
        return {"ok": False, "error": "too_few_rows", "n": len(rows)}
    try:
        from sklearn.neighbors import NearestNeighbors
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    mat = vector_matrix(rows)
    scaler = StandardScaler()
    norm = scaler.fit_transform(mat)
    nn = NearestNeighbors(n_neighbors=min(100, len(rows)), metric="euclidean", algorithm="auto")
    nn.fit(norm)
    return {
        "ok": True,
        "n": len(rows),
        "scaler": scaler,
        "nn": nn,
        "rows": rows,
        "mat": mat,
        "norm": norm,
    }


def query_similarity(
    index: dict[str, Any],
    query_vector: list[float | None] | np.ndarray,
    *,
    k: int = 100,
    assignments: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Return similarity stats vs k nearest historical trades."""
    if not index.get("ok"):
        return {"ok": False, "error": index.get("error") or "no_index"}
    nn = index["nn"]
    scaler = index["scaler"]
    rows: list[dict[str, Any]] = index["rows"]
    k = min(int(k), len(rows))

    q = []
    for i, key in enumerate(VECTOR_KEYS):
        if isinstance(query_vector, dict):
            v = query_vector.get(key)
        else:
            v = query_vector[i] if i < len(query_vector) else None
        q.append(np.nan if v is None else float(v))
    q_arr = np.array(q, dtype=float).reshape(1, -1)
    # impute NaN with scaler mean
    means = getattr(scaler, "mean_", np.zeros(len(VECTOR_KEYS)))
    for j in range(q_arr.shape[1]):
        if not np.isfinite(q_arr[0, j]):
            q_arr[0, j] = float(means[j])
    q_norm = scaler.transform(q_arr)
    dists, idxs = nn.kneighbors(q_norm, n_neighbors=k)
    dists = dists[0]
    idxs = idxs[0]
    # Convert distance → similarity % (1 / (1+d))
    sims = [100.0 / (1.0 + float(d)) for d in dists]
    neighbors = [rows[int(i)] for i in idxs]
    pnls = [float(n["pnl"]) for n in neighbors]
    mae = [float(n["mae"]) for n in neighbors if n.get("mae") is not None]
    mfe = [float(n["mfe"]) for n in neighbors if n.get("mfe") is not None]
    hold = [float(n["hold_sec"]) for n in neighbors if n.get("hold_sec") is not None]
    perf = cluster_performance(pnls, mae=mae or None, mfe=mfe or None, hold=hold or None)

    # closest fingerprint label
    closest_fp = None
    if assignments:
        for n in neighbors:
            tid = int(n.get("trade_id") or 0)
            if tid in assignments:
                closest_fp = assignments[tid]
                break

    avg_sim = round(float(np.mean(sims)), 2) if sims else None
    return {
        "ok": True,
        "k": k,
        "similarity_pct": avg_sim,
        "top_similarity_pct": round(float(sims[0]), 2) if sims else None,
        "historical_pf": perf.get("pf"),
        "historical_ev": perf.get("ev"),
        "historical_wr": perf.get("wr"),
        "avg_hold": perf.get("avg_hold"),
        "avg_mae": perf.get("avg_mae"),
        "avg_mfe": perf.get("avg_mfe"),
        "closest_fingerprint": closest_fp,
        "n_neighbors": len(neighbors),
        "recommendation": "RESEARCH ONLY",
    }


def format_similarity(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"SIMILARITY\nERROR\n{result.get('error')}"
    lines = [
        "SIMILARITY",
        "",
        "Similarity",
        f"  {result.get('similarity_pct')}%",
        "",
        "Closest Fingerprint",
        f"  {result.get('closest_fingerprint') or 'n/a'}",
        "",
        "Historical WR",
        f"  {result.get('historical_wr')}%",
        "",
        "Historical PF",
        f"  {result.get('historical_pf')}",
        "",
        "Historical EV",
        f"  {result.get('historical_ev')}",
        "",
        "Average Hold",
        f"  {result.get('avg_hold')}",
        "",
        "Average MAE",
        f"  {result.get('avg_mae')}",
        "",
        "Average MFE",
        f"  {result.get('avg_mfe')}",
        "",
        "Recommendation",
        f"  {result.get('recommendation')}",
    ]
    return "\n".join(lines)


__all__ = ["build_similarity_index", "format_similarity", "query_similarity"]
