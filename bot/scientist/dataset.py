"""Read-only trade dataset enrichment for Scientist."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.ai_agent.features import btc_direction_label
from bot.perf.feature_store import load_enriched_features
from bot.scientist.constants import SOURCE_TABLE


def load_enriched_trades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Load closed trades with features from trade_features cache."""
    rows = load_enriched_features(conn)
    if rows:
        for row in rows:
            row["btc_direction"] = btc_direction_label(row)
        return rows

    from bot.ai_agent.features import build_signal_features, enrich_for_similarity
    from bot.report.analytics import fetch_v2_trades, trade_pnl

    cached = conn.execute(
        f"""
        SELECT * FROM ai_features
        WHERE source_table = ?
        ORDER BY entry_ts ASC
        """,
        (SOURCE_TABLE,),
    ).fetchall()
    if cached:
        out: list[dict[str, Any]] = []
        for row in cached:
            d = dict(row)
            d["pnl"] = float(d.get("pnl") or 0)
            d["btc_direction"] = btc_direction_label(d)
            out.append(d)
        return out

    trades = fetch_v2_trades(conn, closed_only=True)
    out = []
    for trade in trades:
        base = build_signal_features(conn, trade)
        enriched = enrich_for_similarity(base)
        enriched["pnl"] = trade_pnl(trade)
        enriched["exit_reason"] = trade["exit_reason"]
        enriched["holding_time"] = float(trade["holding_time_seconds"] or 0)
        out.append(enriched)
    return out
