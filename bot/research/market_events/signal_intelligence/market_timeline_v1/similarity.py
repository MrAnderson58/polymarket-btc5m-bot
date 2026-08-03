"""Match current market timeline to historical fingerprint chains."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_timeline_v1.labels import (
    chain_key,
    label_chain,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.windows import (
    vector_matrix,
)


def _jaccard(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    if union == 0:
        return 0.0
    # order-aware bonus
    matches = sum(1 for x, y in zip(a, b) if x == y)
    order = matches / max(len(a), len(b))
    return 0.55 * (inter / union) + 0.45 * order


def match_current(
    rows: list[dict[str, Any]],
    chains: list[dict[str, Any]],
    *,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find closest profitable/ready chain to the latest (or given) timeline."""
    if not rows:
        return {
            "ok": False,
            "similarity_pct": None,
            "closest_chain": None,
            "recommendation": "RESEARCH ONLY",
        }
    probe = current or max(rows, key=lambda r: int(r.get("opened_at") or 0))
    probe_labels = label_chain(probe)
    probe_key = chain_key(probe_labels)

    # Exact chain hit
    exact = next((c for c in chains if c.get("chain") == probe_key), None)
    if exact:
        return {
            "ok": True,
            "similarity_pct": 100.0,
            "closest_chain": exact.get("id"),
            "closest_chain_key": exact.get("chain"),
            "historical_wr": exact.get("wr"),
            "historical_pf": exact.get("pf") if exact.get("pf") is not None else ("inf" if exact.get("pf_inf") else None),
            "historical_ev": exact.get("ev"),
            "confidence": exact.get("confidence"),
            "common_duration": exact.get("common_duration"),
            "recommendation": "RESEARCH ONLY",
            "current": {
                "trade_id": probe.get("trade_id"),
                "symbol": probe.get("symbol"),
                "direction": probe.get("direction"),
                "labels": probe_labels,
            },
        }

    # Label similarity against top chains
    best = None
    best_score = -1.0
    for c in chains[:500]:
        labs = c.get("labels") or []
        score = _jaccard(probe_labels, labs)
        if score > best_score:
            best_score = score
            best = c

    # Numeric kNN fallback among rows sharing chain ids
    if best is None or best_score < 0.15:
        mat = vector_matrix(rows)
        try:
            from sklearn.neighbors import NearestNeighbors
            idx_probe = next(i for i, r in enumerate(rows) if int(r["trade_id"]) == int(probe["trade_id"]))
            nn = NearestNeighbors(n_neighbors=min(50, len(rows)), metric="euclidean")
            nn.fit(mat)
            dist, inds = nn.kneighbors(mat[idx_probe : idx_probe + 1], return_distance=True)
            # map neighbor average distance → similarity
            d = float(np.mean(dist[0][1:])) if dist.shape[1] > 1 else float(dist[0][0])
            sim = max(0.0, min(99.0, 100.0 * (1.0 / (1.0 + d / 8.0))))
            # pick most common chain among neighbors via trade ids
            neighbor_ids = {int(rows[i]["trade_id"]) for i in inds[0]}
            scored = []
            for c in chains:
                tids = set(c.get("trade_ids") or [])
                overlap = len(tids & neighbor_ids)
                if overlap:
                    scored.append((overlap, c))
            scored.sort(key=lambda x: x[0], reverse=True)
            best = scored[0][1] if scored else (chains[0] if chains else None)
            best_score = sim / 100.0
        except Exception:
            pass

    if best is None:
        return {
            "ok": False,
            "similarity_pct": None,
            "closest_chain": None,
            "recommendation": "RESEARCH ONLY",
            "current": {
                "trade_id": probe.get("trade_id"),
                "symbol": probe.get("symbol"),
                "direction": probe.get("direction"),
                "labels": probe_labels,
            },
        }

    return {
        "ok": True,
        "similarity_pct": round(100.0 * float(best_score), 2),
        "closest_chain": best.get("id"),
        "closest_chain_key": best.get("chain"),
        "historical_wr": best.get("wr"),
        "historical_pf": best.get("pf") if best.get("pf") is not None else ("inf" if best.get("pf_inf") else None),
        "historical_ev": best.get("ev"),
        "confidence": best.get("confidence"),
        "common_duration": best.get("common_duration"),
        "recommendation": "RESEARCH ONLY",
        "current": {
            "trade_id": probe.get("trade_id"),
            "symbol": probe.get("symbol"),
            "direction": probe.get("direction"),
            "labels": probe_labels,
        },
    }


__all__ = ["match_current"]
