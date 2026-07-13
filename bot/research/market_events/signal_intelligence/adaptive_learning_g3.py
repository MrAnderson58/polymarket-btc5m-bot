"""Phase G.3 — adaptive weight recommendations (no auto-apply to live weights)."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id

_DEFAULT_WEIGHTS = {
    "funding": 0.18,
    "oi": 0.16,
    "atr": 0.12,
    "trend": 0.22,
    "btc": 0.17,
    "history": 0.15,
}


def _load_current_weights(conn: Any) -> dict[str, float]:
    row = conn.execute(
        "SELECT value FROM market_events_g3_ops_state WHERE key = 'weights_current'",
    ).fetchone()
    if row and row["value"]:
        try:
            return {**_DEFAULT_WEIGHTS, **json.loads(row["value"])}
        except (json.JSONDecodeError, TypeError):
            pass
    return dict(_DEFAULT_WEIGHTS)


def _recommend_weights(
    current: dict[str, float],
    *,
    pnl_pct: float,
    factors: dict[str, Any],
) -> dict[str, float]:
    """Shift weights slightly based on closed signal outcome — recommendations only."""
    rec = dict(current)
    delta = 0.01 if pnl_pct > 0 else -0.005
    if factors.get("funding") is not None:
        rec["funding"] = round(max(0.05, min(0.35, rec["funding"] + delta)), 3)
    if factors.get("oi_rising") is not None:
        rec["oi"] = round(max(0.05, min(0.35, rec["oi"] + delta)), 3)
    if factors.get("atr"):
        rec["atr"] = round(max(0.05, min(0.30, rec["atr"] + delta * 0.5)), 3)
    rec["trend"] = round(max(0.10, min(0.40, rec["trend"] + delta)), 3)
    rec["btc"] = round(max(0.05, min(0.30, rec["btc"] + delta * 0.5)), 3)
    rec["history"] = round(max(0.05, min(0.30, rec["history"] + delta * 0.5)), 3)
    total = sum(rec.values()) or 1.0
    return {k: round(v / total, 3) for k, v in rec.items()}


def record_weight_recommendation_g3(conn: Any, *, signal_id: int, pnl_pct: float) -> int:
    current = _load_current_weights(conn)
    sig = conn.execute(
        """
        SELECT s.reason_json, l.factors_json
        FROM market_live_signals_g3 s
        LEFT JOIN market_liquidity_state_g3 l ON l.snapshot_id = s.snapshot_id
        WHERE s.id = ?
        """,
        (signal_id,),
    ).fetchone()
    factors: dict[str, Any] = {}
    if sig and sig["factors_json"]:
        try:
            factors = json.loads(sig["factors_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    recommended = _recommend_weights(current, pnl_pct=pnl_pct, factors=factors)
    notes = {
        "pnl_pct": pnl_pct,
        "current_weights": current,
        "recommended_weights": recommended,
        "auto_apply": False,
    }
    now = int(time.time())
    return insert_returning_id(
        conn,
        """
        INSERT INTO weight_history_g3 (
          signal_id, funding_weight, oi_weight, atr_weight, trend_weight, btc_weight, history_weight,
          recommended_funding, recommended_oi, recommended_atr, recommended_trend,
          recommended_btc, recommended_history, notes_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            current["funding"], current["oi"], current["atr"],
            current["trend"], current["btc"], current["history"],
            recommended["funding"], recommended["oi"], recommended["atr"],
            recommended["trend"], recommended["btc"], recommended["history"],
            json.dumps(notes), now,
        ),
    )


def latest_weight_recommendations_g3(conn: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT notes_json, created_at FROM weight_history_g3
        ORDER BY created_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            notes = json.loads(r["notes_json"])
        except (json.JSONDecodeError, TypeError):
            notes = {}
        notes["created_at"] = int(r["created_at"])
        out.append(notes)
    return out
