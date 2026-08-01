"""Similarity search: Euclidean, Cosine, DTW, sequence similarity."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

FEATURE_KEYS: tuple[str, ...] = (
    "rsi",
    "atr",
    "funding",
    "oi_delta",
    "fear_greed",
    "trend",
    "ema20_distance",
    "macd",
    "adx",
    "volume",
    "hour",
    "confidence",
    "pnl",
)


def trade_feature_vector(trade: dict[str, Any]) -> np.ndarray:
    vals = []
    for k in FEATURE_KEYS:
        v = trade.get(k)
        try:
            vals.append(float(v) if v is not None else np.nan)
        except Exception:
            vals.append(np.nan)
    # direction / gate one-hots
    vals.append(1.0 if str(trade.get("direction") or "").upper() == "LONG" else 0.0)
    vals.append(1.0 if str(trade.get("gate_decision") or "").upper() == "PASS" else 0.0)
    arr = np.asarray(vals, dtype=np.float64)
    # nan -> column will be filled later
    return arr


def build_matrix(trades: Sequence[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Return (X_imputed, X_raw_with_nan)."""
    raw = np.vstack([trade_feature_vector(t) for t in trades]) if trades else np.zeros((0, 0))
    if raw.size == 0:
        return raw, raw
    X = raw.copy()
    for j in range(X.shape[1]):
        col = X[:, j]
        med = float(np.nanmedian(col)) if np.isfinite(col).any() else 0.0
        col = np.where(np.isfinite(col), col, med)
        sd = float(np.std(col))
        if sd < 1e-12:
            X[:, j] = 0.0
        else:
            X[:, j] = (col - float(np.mean(col))) / sd
    return X, raw


def euclidean_distances(X: np.ndarray, i: int) -> np.ndarray:
    diff = X - X[i]
    return np.sqrt(np.sum(diff * diff, axis=1))


def cosine_distances(X: np.ndarray, i: int) -> np.ndarray:
    a = X[i]
    an = float(np.linalg.norm(a)) + 1e-12
    bn = np.linalg.norm(X, axis=1) + 1e-12
    sims = (X @ a) / (bn * an)
    return 1.0 - sims


def dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Classic DTW on 1-D sequences (price path / feature series)."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")
    dp = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    dp[0, 0] = 0.0
    for i in range(1, n + 1):
        ai = a[i - 1]
        for j in range(1, m + 1):
            cost = abs(float(ai) - float(b[j - 1]))
            dp[i, j] = cost + min(dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1])
    return float(dp[n, m])


def sequence_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Normalized correlation-like similarity in [0,1] (higher=more similar)."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    n = min(len(a), len(b))
    aa, bb = a[:n], b[:n]
    if float(np.std(aa)) < 1e-12 or float(np.std(bb)) < 1e-12:
        return 1.0 if np.allclose(aa, bb) else 0.0
    corr = float(np.corrcoef(aa, bb)[0, 1])
    if not np.isfinite(corr):
        return 0.0
    return max(0.0, min(1.0, (corr + 1.0) / 2.0))


def price_path_from_frames(frames: list[dict[str, Any]]) -> np.ndarray:
    vals = []
    for f in frames:
        p = f.get("price")
        try:
            vals.append(float(p) if p is not None else np.nan)
        except Exception:
            vals.append(np.nan)
    arr = np.asarray(vals, dtype=np.float64)
    if np.isfinite(arr).any():
        med = float(np.nanmedian(arr))
        arr = np.where(np.isfinite(arr), arr, med)
    else:
        arr = np.zeros(len(vals), dtype=np.float64)
    # normalize path relative to entry (index of offset 0 ≈ middle)
    if abs(arr[0]) > 1e-12:
        arr = arr / arr[0] - 1.0
    return arr


def top_k_similar(
    trades: Sequence[dict[str, Any]],
    *,
    query_idx: int,
    k: int = 100,
    metric: str = "euclidean",
    frame_paths: list[np.ndarray] | None = None,
) -> list[dict[str, Any]]:
    """Return top-k similar trade ids with distances for the chosen metric."""
    n = len(trades)
    if n == 0 or query_idx < 0 or query_idx >= n:
        return []
    X, _ = build_matrix(trades)
    metric = (metric or "euclidean").lower()
    if metric == "cosine":
        dist = cosine_distances(X, query_idx)
    elif metric == "dtw":
        if frame_paths is None:
            frame_paths = [np.zeros(1) for _ in trades]
        q = frame_paths[query_idx]
        dist = np.asarray([dtw_distance(q, frame_paths[j]) for j in range(n)], dtype=np.float64)
    elif metric in ("sequence", "seq"):
        if frame_paths is None:
            frame_paths = [np.zeros(1) for _ in trades]
        q = frame_paths[query_idx]
        # distance = 1 - similarity
        dist = np.asarray(
            [1.0 - sequence_similarity(q, frame_paths[j]) for j in range(n)],
            dtype=np.float64,
        )
    else:
        dist = euclidean_distances(X, query_idx)

    dist = dist.copy()
    dist[query_idx] = np.inf
    k = min(k, n - 1)
    if k <= 0:
        return []
    # partial sort
    idx = np.argpartition(dist, kth=min(k, len(dist) - 1))[: k + 5]
    idx = idx[np.argsort(dist[idx])][:k]
    out = []
    for j in idx:
        out.append({
            "trade_id": int(trades[int(j)].get("trade_id") or trades[int(j)].get("id") or j),
            "distance": round(float(dist[int(j)]), 6),
            "metric": metric,
            "symbol": trades[int(j)].get("symbol"),
            "direction": trades[int(j)].get("direction"),
            "pnl": trades[int(j)].get("pnl"),
            "regime": trades[int(j)].get("regime"),
        })
    return out


def batch_similarity_all(
    trades: Sequence[dict[str, Any]],
    *,
    k: int = 100,
    frame_paths: list[np.ndarray] | None = None,
    metrics: tuple[str, ...] = ("euclidean", "cosine"),
) -> list[dict[str, Any]]:
    """For each trade, TOP-k under primary metric (euclidean) + optional cosine ranks."""
    n = len(trades)
    if n == 0:
        return []
    X, _ = build_matrix(trades)
    # Precompute pairwise Euclidean via (optional) chunking for memory
    # For n<=30k, full NxN float32 is ~3.6GB — too big. Use per-row top-k.
    results: list[dict[str, Any]] = []
    for i in range(n):
        sims: dict[str, list[dict[str, Any]]] = {}
        for m in metrics:
            sims[m] = top_k_similar(
                trades, query_idx=i, k=k, metric=m, frame_paths=frame_paths
            )
        # DTW/sequence only for smaller n or sampled
        results.append({
            "trade_id": int(trades[i].get("trade_id") or i),
            "similar": sims.get("euclidean") or [],
            "by_metric": sims,
        })
    return results


def similarity_for_trade(
    trades: Sequence[dict[str, Any]],
    trade_idx: int,
    *,
    k: int = 100,
    frame_paths: list[np.ndarray] | None = None,
) -> dict[str, Any]:
    return {
        "euclidean": top_k_similar(trades, query_idx=trade_idx, k=k, metric="euclidean", frame_paths=frame_paths),
        "cosine": top_k_similar(trades, query_idx=trade_idx, k=k, metric="cosine", frame_paths=frame_paths),
        "dtw": top_k_similar(trades, query_idx=trade_idx, k=min(k, 50), metric="dtw", frame_paths=frame_paths),
        "sequence": top_k_similar(trades, query_idx=trade_idx, k=min(k, 50), metric="sequence", frame_paths=frame_paths),
    }


__all__ = [
    "FEATURE_KEYS",
    "batch_similarity_all",
    "build_matrix",
    "cosine_distances",
    "dtw_distance",
    "euclidean_distances",
    "price_path_from_frames",
    "sequence_similarity",
    "similarity_for_trade",
    "top_k_similar",
    "trade_feature_vector",
]
