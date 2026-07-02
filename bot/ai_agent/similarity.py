"""Similar Trades Engine — kNN over historical signals (observe-only)."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

DEFAULT_K = 100

_NUMERIC_FEATURES = (
    "entry_price",
    "btc_move_15s",
    "btc_move_30s",
    "btc_move_60s",
    "btc_acceleration",
    "spread",
    "seconds_from_start",
    "distance_to_strike",
    "volatility",
)

_CAT_FEATURES = (
    "market_regime",
    "strategy_name",
    "btc_direction",
    "entry_bucket",
    "holding_bucket",
)


def _num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    return float(value)


def _encode_row(row: dict[str, Any], vocab: dict[str, dict[str, int]]) -> list[float]:
    vec: list[float] = [_num(row.get(k)) for k in _NUMERIC_FEATURES]
    for key in _CAT_FEATURES:
        mapping = vocab.get(key, {})
        label = str(row.get(key if key != "market_regime" else "market_regime", ""))
        if key == "strategy_name":
            label = str(row.get("strategy_name", ""))
        elif key == "entry_bucket":
            label = str(row.get("entry_bucket", row.get("entry_price", "")))
        elif key == "holding_bucket":
            label = str(row.get("holding_bucket", "unknown"))
        elif key == "btc_direction":
            label = str(row.get("btc_direction", "flat"))
        elif key == "market_regime":
            label = str(row.get("market_regime") or row.get("regime_label") or "Range")
        vec.append(float(mapping.get(label, 0)))
    return vec


def _build_vocab(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    vocab: dict[str, dict[str, int]] = {k: {} for k in _CAT_FEATURES}
    for row in rows:
        values = {
            "market_regime": str(row.get("market_regime") or row.get("regime_label") or "Range"),
            "strategy_name": str(row.get("strategy_name", "")),
            "btc_direction": str(row.get("btc_direction", "flat")),
            "entry_bucket": str(row.get("entry_bucket", row.get("entry_price", ""))),
            "holding_bucket": str(row.get("holding_bucket", "unknown")),
        }
        for key, label in values.items():
            if label not in vocab[key]:
                vocab[key][label] = len(vocab[key])
    return vocab


def _aggregate_neighbors(neighbors: list[dict[str, Any]]) -> dict[str, Any]:
    if not neighbors:
        return {
            "similar_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_pnl": 0.0,
            "avg_mae": 0.0,
            "avg_mfe": 0.0,
            "top_regime": None,
        }
    pnls = [float(n.get("pnl") or 0) for n in neighbors]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    tp = sum(wins)
    tl = abs(sum(losses))
    maes = [float(n["mae"]) for n in neighbors if n.get("mae") is not None]
    mfes = [float(n["mfe"]) for n in neighbors if n.get("mfe") is not None]
    regimes = [
        str(n.get("market_regime") or n.get("regime_label") or "Range") for n in neighbors
    ]
    top_regime = Counter(regimes).most_common(1)[0][0] if regimes else None
    return {
        "similar_count": len(neighbors),
        "win_rate": len(wins) / len(pnls),
        "profit_factor": tp / tl if tl else float("inf"),
        "avg_pnl": statistics.mean(pnls),
        "avg_mae": statistics.mean(maes) if maes else 0.0,
        "avg_mfe": statistics.mean(mfes) if mfes else 0.0,
        "top_regime": top_regime,
    }


class SimilarTradesEngine:
    """kNN search over prior closed trades (no lookahead)."""

    def __init__(self, *, k: int = DEFAULT_K) -> None:
        self.k = k
        self._rows: list[dict[str, Any]] = []
        self._vocab: dict[str, dict[str, int]] = {}
        self._model = None
        self._matrix = None

    def fit(self, historical: list[dict[str, Any]]) -> None:
        self._rows = list(historical)
        self._vocab = _build_vocab(self._rows)
        if not self._rows:
            self._model = None
            self._matrix = None
            return
        from sklearn.neighbors import NearestNeighbors
        import numpy as np

        matrix = np.array([_encode_row(r, self._vocab) for r in self._rows], dtype=float)
        self._matrix = matrix
        n_neighbors = min(self.k, len(self._rows))
        self._model = NearestNeighbors(n_neighbors=n_neighbors, metric="euclidean")
        self._model.fit(matrix)

    def query(self, signal: dict[str, Any], *, k: int | None = None) -> dict[str, Any]:
        k = k or self.k
        if not self._rows or self._model is None:
            return _aggregate_neighbors([])
        import numpy as np

        vec = np.array([_encode_row(signal, self._vocab)], dtype=float)
        n = min(k, len(self._rows))
        distances, indices = self._model.kneighbors(vec, n_neighbors=n)
        neighbors = [self._rows[int(i)] for i in indices[0]]
        stats = _aggregate_neighbors(neighbors)
        stats["neighbor_indices"] = [int(i) for i in indices[0]]
        stats["avg_distance"] = float(statistics.mean(distances[0])) if len(distances[0]) else 0.0
        return stats
