"""Similarity Engine v2 — expanded kNN feature space."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

DEFAULT_K = 100

_NUMERIC = (
    "entry_price",
    "btc_move_5s",
    "btc_move_10s",
    "btc_move_15s",
    "btc_move_20s",
    "btc_move_30s",
    "btc_move_45s",
    "btc_move_60s",
    "btc_move_90s",
    "btc_acceleration",
    "spread",
    "seconds_from_start",
    "distance_to_strike",
    "volatility",
    "volatility_15s",
    "volatility_30s",
    "volatility_60s",
    "mfe",
    "mae",
    "holding_time",
)

_CATEGORICAL = (
    "market_regime",
    "strategy_name",
    "btc_direction",
    "entry_bucket",
    "holding_bucket",
    "side",
    "outcome",
)


def _num(v: Any, default: float = 0.0) -> float:
    return default if v is None else float(v)


def _encode(row: dict[str, Any], vocab: dict[str, dict[str, int]]) -> list[float]:
    vec = [_num(row.get(k)) for k in _NUMERIC]
    cats = {
        "market_regime": str(row.get("market_regime") or row.get("regime_label") or "Range"),
        "strategy_name": str(row.get("strategy_name", "")),
        "btc_direction": str(row.get("btc_direction", "flat")),
        "entry_bucket": str(row.get("entry_bucket", row.get("entry_price", ""))),
        "holding_bucket": str(row.get("holding_bucket", "unknown")),
        "side": str(row.get("side", "")),
        "outcome": str(row.get("outcome", "unknown")),
    }
    for key in _CATEGORICAL:
        vec.append(float(vocab.get(key, {}).get(cats[key], 0)))
    return vec


def _vocab(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    v: dict[str, dict[str, int]] = {k: {} for k in _CATEGORICAL}
    for row in rows:
        values = {
            "market_regime": str(row.get("market_regime") or row.get("regime_label") or "Range"),
            "strategy_name": str(row.get("strategy_name", "")),
            "btc_direction": str(row.get("btc_direction", "flat")),
            "entry_bucket": str(row.get("entry_bucket", "")),
            "holding_bucket": str(row.get("holding_bucket", "unknown")),
            "side": str(row.get("side", "")),
            "outcome": str(row.get("outcome", "unknown")),
        }
        for key, label in values.items():
            if label not in v[key]:
                v[key][label] = len(v[key])
    return v


def _aggregate(neighbors: list[dict[str, Any]]) -> dict[str, Any]:
    if not neighbors:
        return {
            "similar_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_pnl": 0.0,
            "avg_mae": 0.0,
            "avg_mfe": 0.0,
            "top_regime": None,
            "engine": "v2",
        }
    pnls = [float(n.get("pnl") or 0) for n in neighbors]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    tp, tl = sum(wins), abs(sum(losses))
    maes = [float(n["mae"]) for n in neighbors if n.get("mae") is not None]
    mfes = [float(n["mfe"]) for n in neighbors if n.get("mfe") is not None]
    regimes = [str(n.get("market_regime") or n.get("regime_label") or "?") for n in neighbors]
    return {
        "similar_count": len(neighbors),
        "win_rate": len(wins) / len(pnls),
        "profit_factor": tp / tl if tl else float("inf"),
        "avg_pnl": statistics.mean(pnls),
        "avg_mae": statistics.mean(maes) if maes else 0.0,
        "avg_mfe": statistics.mean(mfes) if mfes else 0.0,
        "top_regime": Counter(regimes).most_common(1)[0][0],
        "engine": "v2",
    }


class SimilarityEngineV2:
    def __init__(self, *, k: int = DEFAULT_K) -> None:
        self.k = k
        self._rows: list[dict[str, Any]] = []
        self._vocab: dict[str, dict[str, int]] = {}
        self._model = None

    def fit(self, historical: list[dict[str, Any]]) -> None:
        self._rows = list(historical)
        self._vocab = _vocab(self._rows)
        if not self._rows:
            self._model = None
            return
        from sklearn.neighbors import NearestNeighbors
        import numpy as np

        matrix = np.array([_encode(r, self._vocab) for r in self._rows], dtype=float)
        n = min(self.k, len(self._rows))
        self._model = NearestNeighbors(n_neighbors=n, metric="euclidean")
        self._model.fit(matrix)

    def query(self, signal: dict[str, Any], *, k: int | None = None) -> dict[str, Any]:
        if not self._rows or self._model is None:
            return _aggregate([])
        import numpy as np

        k = k or self.k
        vec = np.array([_encode(signal, self._vocab)], dtype=float)
        n = min(k, len(self._rows))
        dist, idx = self._model.kneighbors(vec, n_neighbors=n)
        neighbors = [self._rows[int(i)] for i in idx[0]]
        stats = _aggregate(neighbors)
        stats["avg_distance"] = float(statistics.mean(dist[0])) if len(dist[0]) else 0.0
        return stats
