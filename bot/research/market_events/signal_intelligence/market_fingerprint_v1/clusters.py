"""Cluster WIN/LOSS/BE fingerprints."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
    VECTOR_KEYS,
    vector_matrix,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)


def _normalize(mat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = np.mean(mat, axis=0)
    sd = np.std(mat, axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    return (mat - mu) / sd, mu, sd


def build_clusters(
    rows: list[dict[str, Any]],
    *,
    k_per_bucket: int = 8,
    min_n: int = 40,
) -> dict[str, Any]:
    """MiniBatchKMeans fingerprints per WIN/LOSS/BE."""
    if not rows:
        return {"clusters": [], "assignments": {}, "n": 0}

    try:
        from sklearn.cluster import MiniBatchKMeans
    except Exception:
        return {"clusters": [], "assignments": {}, "n": 0, "error": "sklearn_missing"}

    by_res: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_res[str(r.get("result") or "BE")].append(r)

    clusters: list[dict[str, Any]] = []
    assignments: dict[int, str] = {}

    for result in ("WIN", "LOSS", "BE"):
        items = by_res.get(result) or []
        if len(items) < max(min_n, k_per_bucket * 3):
            continue
        mat = vector_matrix(items)
        norm, mu, sd = _normalize(mat)
        k = min(k_per_bucket, max(2, len(items) // min_n))
        km = MiniBatchKMeans(n_clusters=k, random_state=7, batch_size=min(2048, len(items)), n_init=3)
        labels = km.fit_predict(norm)
        for local_i, lab in enumerate(labels):
            tid = int(items[local_i]["trade_id"])
            name = f"{result}_{lab:02d}"
            # refine name later with regime/dir
            assignments[tid] = name

        for lab in range(k):
            idxs = [i for i, x in enumerate(labels) if int(x) == lab]
            subset = [items[i] for i in idxs]
            if len(subset) < max(15, min_n // 2):
                continue
            pnls = [float(s["pnl"]) for s in subset]
            mae = [float(s["mae"]) for s in subset if s.get("mae") is not None]
            mfe = [float(s["mfe"]) for s in subset if s.get("mfe") is not None]
            hold = [float(s["hold_sec"]) for s in subset if s.get("hold_sec") is not None]
            perf = cluster_performance(pnls, mae=mae or None, mfe=mfe or None, hold=hold or None)
            dirs = Counter(str(s.get("direction") or "") for s in subset)
            regs = Counter(str(s.get("regime") or "UNK") for s in subset)
            top_dir = dirs.most_common(1)[0][0] if dirs else "UNK"
            top_reg = regs.most_common(1)[0][0] if regs else "UNK"
            # readable fingerprint id
            fp_id = f"{top_reg}_{top_dir}_{result[0]}{lab:02d}".replace(" ", "")
            # center in original feature space
            center_norm = km.cluster_centers_[lab]
            center = (center_norm * sd + mu).tolist()
            center_map = {VECTOR_KEYS[i]: round(float(center[i]), 6) for i in range(len(VECTOR_KEYS))}
            # rewrite assignments with fp_id
            for i in idxs:
                assignments[int(items[i]["trade_id"])] = fp_id
            clusters.append({
                "id": fp_id,
                "result_bucket": result,
                "n": len(subset),
                "direction_mode": top_dir,
                "regime_mode": top_reg,
                "wr": perf.get("wr"),
                "pf": perf.get("pf"),
                "ev": perf.get("ev"),
                "sharpe": perf.get("sharpe"),
                "max_dd": perf.get("max_dd"),
                "ci_lo": perf.get("ci_lo"),
                "ci_hi": perf.get("ci_hi"),
                "avg_mae": perf.get("avg_mae"),
                "avg_mfe": perf.get("avg_mfe"),
                "avg_hold": perf.get("avg_hold"),
                "center": center_map,
            })

    clusters.sort(key=lambda c: (float(c.get("pf") or 0), float(c.get("ev") or 0), int(c.get("n") or 0)), reverse=True)
    return {
        "clusters": clusters,
        "assignments": assignments,
        "n": len(rows),
        "n_clusters": len(clusters),
    }


__all__ = ["build_clusters"]
