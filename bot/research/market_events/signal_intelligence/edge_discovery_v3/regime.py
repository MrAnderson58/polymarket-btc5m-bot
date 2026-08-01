"""Automatic market regime clustering (no hardcoded regime labels)."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.edge_discovery_v3.dataset import (
    NUMERIC_FEATURES,
)


def _feature_matrix(numeric: dict[str, np.ndarray]) -> tuple[np.ndarray, list[str]]:
    cols: list[str] = []
    mats: list[np.ndarray] = []
    n = None
    for f in NUMERIC_FEATURES:
        arr = numeric.get(f)
        if arr is None:
            continue
        if n is None:
            n = len(arr)
        # Fill nan with column median
        med = float(np.nanmedian(arr)) if np.isfinite(arr).any() else 0.0
        filled = np.where(np.isfinite(arr), arr, med)
        # Skip constant
        if float(np.std(filled)) < 1e-12:
            continue
        cols.append(f)
        mats.append(filled)
    if not mats or n is None:
        return np.zeros((0, 0)), []
    X = np.column_stack(mats)
    # z-score
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    return (X - mu) / sd, cols


def cluster_regimes(
    numeric: dict[str, np.ndarray],
    pnls: np.ndarray,
    *,
    max_k: int = 6,
    random_state: int = 42,
) -> dict[str, Any]:
    """KMeans (+ optional HDBSCAN) with automatic k via silhouette when possible."""
    X, cols = _feature_matrix(numeric)
    if X.shape[0] < 20 or X.shape[1] < 2:
        return {
            "ok": False,
            "method": "none",
            "n_regimes": 0,
            "features_used": cols,
            "regimes": [],
            "embedding": None,
        }

    labels = None
    method = "kmeans"
    best_k = 2
    try:
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score

        best_score = -1.0
        best_labels = None
        k_max = min(max_k, max(2, X.shape[0] // 15))
        for k in range(2, k_max + 1):
            km = KMeans(n_clusters=k, n_init=10, random_state=random_state)
            lab = km.fit_predict(X)
            if len(set(lab)) < 2:
                continue
            try:
                sc = float(silhouette_score(X, lab, sample_size=min(2000, len(X))))
            except Exception:
                sc = -1.0
            if sc > best_score:
                best_score = sc
                best_labels = lab
                best_k = k
        labels = best_labels
        method = f"kmeans_k={best_k}_sil={round(best_score, 3)}"
    except Exception as exc:
        # Fallback: quantile on first PC
        method = f"pca_quantile_fallback:{exc}"
        try:
            from sklearn.decomposition import PCA

            pc = PCA(n_components=1, random_state=random_state).fit_transform(X).ravel()
            q33, q66 = np.quantile(pc, [0.33, 0.66])
            labels = np.where(pc <= q33, 0, np.where(pc >= q66, 2, 1))
            best_k = 3
        except Exception:
            labels = np.zeros(X.shape[0], dtype=int)
            best_k = 1

    # Optional HDBSCAN overlay note
    hdbscan_note = None
    try:
        import hdbscan  # type: ignore

        clusterer = hdbscan.HDBSCAN(min_cluster_size=max(10, X.shape[0] // 20))
        hlab = clusterer.fit_predict(X)
        n_h = len(set(hlab)) - (1 if -1 in hlab else 0)
        hdbscan_note = f"hdbscan_clusters={n_h}"
    except Exception:
        hdbscan_note = "hdbscan_unavailable"

    # Embedding for report (PCA 2D; UMAP if installed)
    embedding = None
    embed_method = "pca2"
    try:
        try:
            import umap  # type: ignore

            reducer = umap.UMAP(n_components=2, random_state=random_state)
            embedding = reducer.fit_transform(X).tolist()
            embed_method = "umap2"
        except Exception:
            from sklearn.decomposition import PCA

            embedding = PCA(n_components=2, random_state=random_state).fit_transform(X).tolist()
    except Exception:
        embedding = None

    regimes: list[dict[str, Any]] = []
    if labels is not None:
        for rid in sorted(set(int(x) for x in labels)):
            m = labels == rid
            n = int(m.sum())
            if n == 0:
                continue
            # Characteristic features: largest |mean|
            means = X[m].mean(axis=0)
            order = np.argsort(-np.abs(means))[:5]
            top_feats = [
                {"feature": cols[j], "z_mean": round(float(means[j]), 3)}
                for j in order
            ]
            regimes.append({
                "regime_id": int(rid),
                "label": f"auto_regime_{rid}",
                "n": n,
                "share": round(n / len(labels), 4),
                "mean_pnl": round(float(pnls[m].mean()), 4),
                "winrate": round(100.0 * float(np.mean(pnls[m] > 0)), 2),
                "top_features": top_feats,
            })

    return {
        "ok": True,
        "method": method,
        "hdbscan": hdbscan_note,
        "embedding_method": embed_method,
        "n_regimes": len(regimes),
        "features_used": cols,
        "regimes": regimes,
        "embedding_sample": (embedding[:50] if embedding else None),
        "labels_histogram": {
            str(r["regime_id"]): r["n"] for r in regimes
        },
    }


__all__ = ["cluster_regimes"]
