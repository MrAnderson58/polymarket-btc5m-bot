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
    from bot.research.mtf.discovery import discover_active_htf_markets
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
        quotes = _fetch_htf_quotes(ref.slug)
        if quotes:
            row[f"market_{prefix}_yes_bid"] = quotes.get("yes_bid")
            row[f"market_{prefix}_yes_ask"] = quotes.get("yes_ask")
            row[f"market_{prefix}_no_bid"] = quotes.get("no_bid")
            row[f"market_{prefix}_no_ask"] = quotes.get("no_ask")
            row[f"market_{prefix}_seconds_left"] = quotes.get("seconds_left")
            row[f"market_{prefix}_strike"] = quotes.get("strike") or strike

    row["raw_json"] = {"htf_discovery": {k: v.slug if v else None for k, v in htf.items()}}
    insert_snapshot(conn, row)
    logger.debug("MTF snapshot collected | 5m=%s 15m=%s", market_5m_slug, row.get("market_15m_slug"))


def _fetch_htf_quotes(slug: str) -> dict[str, Any] | None:
    try:
        from bot.market_scanner import get_token_ids_for_market_slug, get_token_quotes, _get_clob_client
        token_ids = get_token_ids_for_market_slug(slug)
        if not token_ids:
            return None
        client = _get_clob_client()
        yes_q = get_token_quotes(client, token_ids[0])
        no_q = get_token_quotes(client, token_ids[1])
        return {
            "yes_bid": yes_q.bid,
            "yes_ask": yes_q.ask,
            "no_bid": no_q.bid,
            "no_ask": no_q.ask,
        }
    except Exception as exc:
        logger.debug("HTF quote fetch failed for %s: %s", slug, exc)
        return None
