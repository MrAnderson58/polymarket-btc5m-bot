"""Bounded deterministic context bundle for AI analyst shadow layer."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.ai_analyst.config import AI_MAX_CONTEXT_ITEMS
from bot.research.market_events.lifecycle_decisions import load_lifecycle_decisions


def _col(row: Any, name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


def build_context_bundle(conn: Any, event_id: int) -> dict[str, Any]:
    """Assemble top-N bounded context; no unbounded history dump."""
    evt = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not evt:
        return {"event_id": event_id, "error": "event_not_found"}

    pending = conn.execute(
        "SELECT * FROM market_events_pending_shocks WHERE event_id = ?",
        (event_id,),
    ).fetchone()

    lifecycle = load_lifecycle_decisions(conn, event_id)
    rev_status = {}
    for d in lifecycle:
        if d["stage"].startswith("REVERSAL_"):
            rev_status[d["stage"]] = d["status"]

    snapshots = conn.execute(
        """
        SELECT offset_seconds, price, return_from_event, volume, spread_bps
        FROM market_event_snapshots WHERE event_id = ?
        ORDER BY offset_seconds LIMIT 20
        """,
        (event_id,),
    ).fetchall()

    ctx_rows = conn.execute(
        """
        SELECT id, context_type, source, source_record_id, context_ts,
               time_delta_seconds, relevance_score, context_json
        FROM market_event_context WHERE event_id = ?
        ORDER BY relevance_score DESC, context_ts DESC
        LIMIT ?
        """,
        (event_id, AI_MAX_CONTEXT_ITEMS),
    ).fetchall()

    telegram_items = []
    for r in ctx_rows:
        telegram_items.append({
            "context_id": r["id"],
            "type": r["context_type"],
            "source": r["source"],
            "source_record_id": r["source_record_id"],
            "context_ts": r["context_ts"],
            "time_delta_sec": r["time_delta_seconds"],
            "relevance": r["relevance_score"],
            "meta": json.loads(r["context_json"] or "{}"),
        })

    extra_tg = _fetch_agent_telegram_context(
        symbol=evt["symbol"],
        event_ts=int(evt["event_ts"]),
        limit=max(0, AI_MAX_CONTEXT_ITEMS - len(telegram_items)),
    )
    telegram_items.extend(extra_tg)

    research_stats = _fetch_research_outcomes(symbol=evt["symbol"])

    bundle = {
        "event_id": event_id,
        "market_event": {
            "symbol": evt["symbol"],
            "asset_class": _col(evt, "asset_class"),
            "direction": evt["direction"],
            "return_pct": evt["return_pct"],
            "trigger_window_seconds": evt["trigger_window_seconds"],
            "volume_zscore": _col(evt, "volume_zscore"),
            "btc_return_pct": _col(evt, "btc_return_pct"),
            "market_return_pct": _col(evt, "market_return_pct"),
            "relative_return_pct": _col(evt, "relative_return_pct"),
            "classification": evt["classification"],
            "cross_classification": _col(evt, "cross_classification"),
            "session_regime": _col(evt, "session_regime"),
            "basis_bps": _col(evt, "basis_bps"),
            "reference_return_pct": _col(evt, "reference_return_pct"),
            "phase": evt["phase"],
        },
        "price_structure": {
            "snapshots": [dict(s) for s in snapshots],
            "pending_extreme_price": pending["shock_extreme_price"] if pending else None,
            "path_from_extreme": json.loads(pending["path_from_extreme_json"] or "null") if pending else None,
            "confirm_latency_sec": pending["confirm_latency_sec"] if pending else None,
            "confirmed_reversal": pending["confirmed_reversal"] if pending else None,
            "reversal_lifecycle": rev_status,
        },
        "telegram_context": telegram_items[:AI_MAX_CONTEXT_ITEMS],
        "historical_research": research_stats,
        "cross_asset": {
            "btc_return_pct": _col(evt, "btc_return_pct"),
            "market_return_pct": _col(evt, "market_return_pct"),
            "relative_return_pct": _col(evt, "relative_return_pct"),
            "cross_classification": _col(evt, "cross_classification"),
            "basis_bps": _col(evt, "basis_bps"),
        },
    }
    return bundle


def _fetch_agent_telegram_context(
    *,
    symbol: str,
    event_ts: int,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    items: list[dict[str, Any]] = []
    try:
        from bot.research.futures_agent.db import agent_connection
        from bot.research.futures_agent.research_utils import parse_symbols_json
        from bot.research.futures_agent.telegram_inbound_bridge import INBOUND_CHANNEL_PREFIX

        window = 6 * 3600
        with agent_connection() as agent:
            rows = agent.execute(
                """
                SELECT p.id, p.channel_name, p.message_ts, p.content_type,
                       p.symbols_json, p.raw_text, 'trader_post' AS src
                FROM futures_agent_trader_posts p
                WHERE p.message_ts BETWEEN ? AND ?
                  AND (p.symbols_json LIKE ? OR p.raw_text LIKE ?)
                ORDER BY p.message_ts DESC
                LIMIT ?
                """,
                (
                    event_ts - window, event_ts,
                    f'%"{symbol}"%', f"%{symbol}%", limit,
                ),
            ).fetchall()
            for r in rows:
                items.append({
                    "context_id": f"post:{r['id']}",
                    "type": r["content_type"],
                    "source": r["channel_name"],
                    "source_record_id": str(r["id"]),
                    "context_ts": r["message_ts"],
                    "preview": (r["raw_text"] or "")[:200],
                    "symbols": parse_symbols_json(r["symbols_json"]),
                    "bridge": INBOUND_CHANNEL_PREFIX in (r["channel_name"] or ""),
                })
    except Exception:
        pass
    return items


def _fetch_research_outcomes(symbol: str) -> dict[str, Any]:
    out: dict[str, Any] = {"symbol": symbol, "cohorts": []}
    try:
        from bot.research.futures_agent.db import agent_connection

        with agent_connection() as agent:
            for direction in ("LONG", "SHORT"):
                row = agent.execute(
                    """
                    SELECT COUNT(*) AS n,
                           AVG(o.directional_return_pct) AS avg_ret
                    FROM futures_agent_research_signal_outcomes o
                    WHERE o.symbol = ? AND o.direction = ?
                    """,
                    (symbol, direction),
                ).fetchone()
                if row and int(row["n"] or 0) >= 5:
                    out["cohorts"].append({
                        "direction": direction,
                        "sample_size": int(row["n"]),
                        "avg_return_pct": row["avg_ret"],
                    })
    except Exception:
        pass
    return out
