"""Incremental Research Lake sync: S42 CLOSED → lake (lag → 0)."""

from __future__ import annotations

import logging
import time
from typing import Any

from bot.research.market_events.signal_intelligence.research_lake_v1.builder import (
    build_research_lake_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    LAKE_TABLE,
    ensure_research_lake_schema,
)

logger = logging.getLogger(__name__)


def lake_lag(conn: Any) -> dict[str, int]:
    ensure_research_lake_schema(conn)
    try:
        n_closed = int(
            conn.execute(
                "SELECT COUNT(*) FROM market_events_paper_trades_s42 "
                "WHERE status='CLOSED' AND pnl_pct IS NOT NULL"
            ).fetchone()[0]
        )
    except Exception:
        n_closed = 0
    try:
        n_lake = int(conn.execute(f"SELECT COUNT(*) FROM {LAKE_TABLE}").fetchone()[0])
    except Exception:
        n_lake = 0
    return {
        "n_s42_closed": n_closed,
        "n_lake": n_lake,
        "lag": max(0, n_closed - n_lake),
    }


def sync_research_lake_incremental(
    conn: Any,
    *,
    write_reports: bool = False,
    materialize_s40: bool = False,
) -> dict[str, Any]:
    """
    Push every CLOSED S42 trade missing from the lake.
    Uses builder incremental mode (id > max lake trade_id), then a catch-up
    full pass only for gaps when lag remains (rare id holes).
    """
    t0 = time.time()
    before = lake_lag(conn)
    # Incremental first
    out = build_research_lake_v1(
        conn,
        full=False,
        write_reports=write_reports,
        materialize_s40=materialize_s40,
        print_fn=lambda *_a, **_k: None,
        profile=False,
    )
    after = lake_lag(conn)
    # If holes remain (CLOSED id below max lake but missing), do targeted upsert
    if after["lag"] > 0:
        missing_ids = [
            int(r[0])
            for r in conn.execute(
                f"""
                SELECT p.id FROM market_events_paper_trades_s42 p
                LEFT JOIN {LAKE_TABLE} l ON l.trade_id = p.id
                WHERE p.status='CLOSED' AND p.pnl_pct IS NOT NULL AND l.trade_id IS NULL
                ORDER BY p.id ASC
                LIMIT 5000
                """
            ).fetchall()
        ]
        if missing_ids:
            # Full rebuild of missing via incremental from 0 is heavy; use full=False
            # after temporarily lowering — simplest reliable fix: full=True once.
            out2 = build_research_lake_v1(
                conn,
                full=True,
                write_reports=False,
                materialize_s40=False,
                print_fn=lambda *_a, **_k: None,
                profile=False,
            )
            out["catchup"] = out2
            after = lake_lag(conn)

    return {
        "ok": after["lag"] == 0,
        "before": before,
        "after": after,
        "lag_before": before["lag"],
        "lag_after": after["lag"],
        "builder": {
            "inserted": out.get("inserted"),
            "updated": out.get("updated"),
            "mode": out.get("mode"),
        },
        "elapsed_sec": round(time.time() - t0, 3),
    }


def sync_closed_trade_to_lake(conn: Any, trade_id: int) -> dict[str, Any]:
    """Best-effort single-trade sync after S42 close (research-only)."""
    try:
        # Incremental sync is cheap when lake is near head
        return sync_research_lake_incremental(conn, write_reports=False, materialize_s40=False)
    except Exception as exc:
        logger.debug("sync_closed_trade_to_lake failed trade_id=%s: %s", trade_id, exc)
        return {"ok": False, "error": str(exc), "trade_id": int(trade_id)}


__all__ = [
    "lake_lag",
    "sync_closed_trade_to_lake",
    "sync_research_lake_incremental",
]
