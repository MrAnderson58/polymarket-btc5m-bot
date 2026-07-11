"""Phase F.2 Task G — multi-factor historical similarity search."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HistoricalSimilarityV2Result:
    count: int
    reversal_count: int
    reversal_rate: float
    matches: list[dict[str, Any]]


def _feature_vector(
    conn: Any,
    *,
    event_id: int,
    ret: float,
    vol_z: float,
    structure_labels: list[str],
) -> dict[str, float | str | None]:
    exch = conn.execute(
        "SELECT funding, open_interest, atr FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    return {
        "ret": ret,
        "vol_z": vol_z,
        "atr": float(exch["atr"]) if exch and exch["atr"] else None,
        "funding": float(exch["funding"]) if exch and exch["funding"] is not None else None,
        "oi": float(exch["open_interest"]) if exch and exch["open_interest"] else None,
        "structure": ",".join(sorted(structure_labels)),
    }


def _similarity_score(cur: dict[str, Any], hist: dict[str, Any]) -> float:
    score = 0.0
    ret_d = abs(cur["ret"] - hist["ret"])
    score += max(0.0, 1.0 - ret_d / max(abs(cur["ret"]), 2.0)) * 0.35

    if cur.get("vol_z") is not None and hist.get("vol_z") is not None:
        vz = abs(float(cur["vol_z"]) - float(hist["vol_z"]))
        score += max(0.0, 1.0 - vz / 3.0) * 0.15

    if cur.get("atr") and hist.get("atr"):
        ar = abs(float(cur["atr"]) - float(hist["atr"])) / max(float(cur["atr"]), 1e-9)
        score += max(0.0, 1.0 - ar) * 0.15

    if cur.get("funding") is not None and hist.get("funding") is not None:
        fd = abs(float(cur["funding"]) - float(hist["funding"]))
        score += max(0.0, 1.0 - fd / 0.05) * 0.1

    if cur.get("oi") and hist.get("oi"):
        od = abs(float(cur["oi"]) - float(hist["oi"])) / max(float(cur["oi"]), 1.0)
        score += max(0.0, 1.0 - min(od, 1.0)) * 0.1

    if cur.get("structure") and hist.get("structure"):
        cs = set(str(cur["structure"]).split(","))
        hs = set(str(hist["structure"]).split(","))
        if cs and hs:
            score += len(cs & hs) / max(len(cs | hs), 1) * 0.15

    return round(min(1.0, score), 3)


def search_historical_similarity_v2(
    conn: Any,
    *,
    symbol: str,
    event_id: int,
    shock_return_pct: float,
    volume_zscore: float,
    structure_labels: list[str],
    min_score: float = 0.45,
    limit: int = 100,
) -> HistoricalSimilarityV2Result:
    cur = _feature_vector(
        conn,
        event_id=event_id,
        ret=abs(shock_return_pct),
        vol_z=volume_zscore,
        structure_labels=structure_labels,
    )

    rows = conn.execute(
        """
        SELECT e.id, e.return_pct, e.volume_zscore, e.direction, p.confirmed_reversal
        FROM market_events e
        LEFT JOIN market_events_pending_shocks p ON p.event_id = e.id
        WHERE e.symbol = ? AND e.id != ?
        ORDER BY e.event_ts DESC
        LIMIT 500
        """,
        (symbol, event_id),
    ).fetchall()

    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        hist = _feature_vector(
            conn,
            event_id=int(r["id"]),
            ret=abs(float(r["return_pct"] or 0)),
            vol_z=abs(float(r["volume_zscore"] or 0)),
            structure_labels=structure_labels,
        )
        sim = _similarity_score(cur, hist)
        if sim >= min_score:
            scored.append((sim, {
                "event_id": int(r["id"]),
                "return_pct": round(float(r["return_pct"] or 0), 2),
                "similarity": sim,
                "confirmed_reversal": r["confirmed_reversal"],
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    matches = [m for _, m in scored[:limit]]
    rev_count = sum(1 for m in matches if m.get("confirmed_reversal"))
    count = len(matches)
    rate = rev_count / count if count else 0.5

    return HistoricalSimilarityV2Result(
        count=count,
        reversal_count=rev_count,
        reversal_rate=round(rate, 3),
        matches=matches[:10],
    )
