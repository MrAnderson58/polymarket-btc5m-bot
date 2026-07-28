"""Load closed S55 paper trades for research packs."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.expectancy_intelligence.row_features import (
    row_to_features,
)
from bot.research.market_events.expectancy_intelligence.stats import safe_float

_TABLE = "market_events_trade_features_s55"


def load_closed_s55_trades(conn: Any, *, limit: int = 50000) -> list[dict[str, Any]]:
    try:
        raw = conn.execute(
            f"""
            SELECT * FROM {_TABLE}
            WHERE closed_at IS NOT NULL AND pnl_pct IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except Exception:
        return []
    trades: list[dict[str, Any]] = []
    for row in raw:
        feats = row_to_features(row)
        pnl = safe_float(row["pnl_pct"])
        if pnl is None:
            continue
        conf = feats.get("confidence") or feats.get("decision_confidence")
        closed_at = None
        try:
            if "closed_at" in row.keys() and row["closed_at"] is not None:
                closed_at = int(row["closed_at"])
        except (TypeError, ValueError):
            closed_at = None
        neighbor_ev = None
        try:
            if "gate_expected_pnl_pct" in row.keys():
                neighbor_ev = safe_float(row["gate_expected_pnl_pct"])
        except Exception:
            neighbor_ev = None
        if neighbor_ev is None:
            neighbor_ev = safe_float(feats.get("gate_expected_pnl_pct"))
        trades.append(
            {
                "pnl_pct": pnl,
                "win": str(row["result"] or "").upper() == "WIN",
                "mfe_pct": safe_float(row["mfe_pct"]) if "mfe_pct" in row.keys() else None,
                "mae_pct": safe_float(row["mae_pct"]) if "mae_pct" in row.keys() else None,
                "duration_sec": int(row["duration_sec"]) if row["duration_sec"] is not None else None,
                "reached_tp1": int(row["reached_tp1"] or 0) if "reached_tp1" in row.keys() else 0,
                "stopped": int(row["stopped"] or 0) if "stopped" in row.keys() else 0,
                "market_regime": feats.get("market_regime"),
                "funding": safe_float(feats.get("funding")),
                "oi_delta": safe_float(feats.get("oi_delta")),
                "trend": safe_float(feats.get("trend")),
                "volatility": safe_float(feats.get("volatility")),
                "fear_greed": safe_float(feats.get("fear_greed")),
                "ai_score": safe_float(feats.get("ai_score")),
                "confidence": safe_float(conf),
                "neighbor_ev": neighbor_ev,
                "closed_at": closed_at,
                "symbol": feats.get("symbol"),
                "direction": feats.get("direction"),
            }
        )
    return trades
