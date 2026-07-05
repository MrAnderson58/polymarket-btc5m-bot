"""Observe-only multi-timeframe snapshot collector."""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)


def collect_mtf_snapshot(
    conn: sqlite3.Connection,
    *,
    market_5m_slug: str,
    btc_price: float,
    yes_bid: float,
    yes_ask: float,
    no_bid: float,
    no_ask: float,
    strike: float | None = None,
    seconds_left: int | None = None,
) -> None:
    """Collect synchronized 5m + HTF Polymarket snapshot. Observe-only."""
    from bot.research.mtf.discovery import discover_active_htf_markets, seconds_left_for_ref
    from bot.research.mtf.quotes import fetch_mtf_market_quotes
    from bot.research.mtf.snapshots import ensure_tables, insert_snapshot

    ensure_tables(conn)
    now_ts = int(time.time())

    row: dict[str, Any] = {
        "timestamp": now_ts,
        "btc_price": btc_price,
        "market_5m_slug": market_5m_slug,
        "market_5m_yes_ask": yes_ask,
        "market_5m_no_ask": no_ask,
    }

    htf = discover_active_htf_markets(now_ts)
    for tf, prefix in [("15m", "15m"), ("1h", "1h"), ("daily", "daily")]:
        ref = htf.get(tf)
        if not ref:
            continue
        row[f"market_{prefix}_slug"] = ref.slug
        quotes = fetch_mtf_market_quotes(ref.slug)
        if quotes:
            row[f"market_{prefix}_yes_bid"] = quotes.get("yes_bid")
            row[f"market_{prefix}_yes_ask"] = quotes.get("yes_ask")
            row[f"market_{prefix}_no_bid"] = quotes.get("no_bid")
            row[f"market_{prefix}_no_ask"] = quotes.get("no_ask")
        row[f"market_{prefix}_seconds_left"] = seconds_left_for_ref(ref, now_ts)
        row[f"market_{prefix}_strike"] = strike

    row["raw_json"] = {"htf_discovery": {k: v.slug if v else None for k, v in htf.items()}}
    insert_snapshot(conn, row)
    logger.debug(
        "MTF snapshot collected | 5m=%s 15m=%s 1h=%s daily=%s",
        market_5m_slug,
        row.get("market_15m_slug"),
        row.get("market_1h_slug"),
        row.get("market_daily_slug"),
    )
