"""Counterfactual: virtual outcomes if expectancy filter were ignored (research only)."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.expectancy_intelligence.row_features import (
    row_to_features,
)
from bot.research.market_events.expectancy_intelligence.stats import mean
from bot.research.market_events.signal_intelligence.trade_intelligence_s55 import (
    GATE_NEGATIVE_EXPECTANCY,
    estimate_from_neighbors,
    find_similar_trades,
)

_TABLE = "market_events_trade_features_s55"


def _majority_outcome(neighbors: list[dict[str, Any]]) -> str:
    wins = sum(1 for n in neighbors if str(n.get("result") or "").upper() == "WIN")
    losses = sum(1 for n in neighbors if str(n.get("result") or "").upper() == "LOSS")
    if wins > losses:
        return "WIN"
    if losses > wins:
        return "LOSS"
    return "BREAKEVEN"


def build_counterfactual(conn: Any, *, since_hours: float = 24.0) -> dict[str, Any]:
    since_ts = int(time.time()) - int(since_hours * 3600)
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM {_TABLE}
            WHERE created_at >= ? AND gate_decision = ?
            ORDER BY created_at DESC
            """,
            (since_ts, GATE_NEGATIVE_EXPECTANCY),
        ).fetchall()
    except Exception:
        rows = []
    items: list[dict[str, Any]] = []
    for row in rows:
        feats = row_to_features(row)
        neighbors = find_similar_trades(conn, feats)
        est = estimate_from_neighbors(neighbors)
        mfes = [float(n.get("mfe_pct") or 0.0) for n in neighbors]
        maes = [float(n.get("mae_pct") or 0.0) for n in neighbors]
        avg_mfe = mean(mfes) if mfes else float(est.get("avg_mfe") or 0.0)
        avg_mae = mean(maes) if maes else float(est.get("avg_mae") or 0.0)
        max_excursion = max(abs(avg_mfe), abs(avg_mae))
        p_tp1 = float(est.get("p_tp1") or 0.0)
        p_sl = float(est.get("p_sl") or 0.0)
        items.append(
            {
                "symbol": str(row["symbol"] or ""),
                "direction": str(row["direction"] or ""),
                "virtual_pnl_pct": float(est.get("expected_pnl_pct") or 0.0),
                "mfe_pct": round(avg_mfe, 4),
                "mae_pct": round(avg_mae, 4),
                "max_excursion_pct": round(max_excursion, 4),
                "would_tp_reach": p_tp1 >= 50.0,
                "would_sl_reach": p_sl >= 50.0,
                "p_tp1": p_tp1,
                "p_sl": p_sl,
                "final_outcome": _majority_outcome(neighbors),
                "neighbor_n": int(est.get("n") or 0),
            }
        )
    return {"since_hours": since_hours, "n": len(items), "items": items}


def format_counterfactual(conn: Any, *, since_hours: float = 24.0) -> str:
    data = build_counterfactual(conn, since_hours=since_hours)
    lines = [
        "COUNTERFACTUAL (expectancy filter ignored — neighbor proxy, no execution)",
        f"  window_hours={data['since_hours']:.0f}  n={data['n']}",
        "",
    ]
    for it in data["items"]:
        lines.append(
            f"{it['symbol']} {it['direction']}  virtual_pnl={it['virtual_pnl_pct']:+.3f}%  "
            f"MFE={it['mfe_pct']:+.3f}% MAE={it['mae_pct']:+.3f}% max_exc={it['max_excursion_pct']:.3f}%  "
            f"TP>{'Y' if it['would_tp_reach'] else 'N'} SL>{'Y' if it['would_sl_reach'] else 'N'}  "
            f"outcome={it['final_outcome']} (n={it['neighbor_n']})"
        )
    if not data["items"]:
        lines.append("  (no NEGATIVE_EXPECTANCY candidates in window)")
    return "\n".join(lines)
