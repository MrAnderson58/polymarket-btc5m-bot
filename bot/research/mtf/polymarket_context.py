"""Polymarket HTF context from snapshots or historical DB — no look-ahead."""

from __future__ import annotations

import sqlite3

from bot.research.mtf.models import PolymarketTfContext


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2
    if ask is not None and ask > 0:
        return ask
    if bid is not None and bid > 0:
        return bid
    return None


def _spread(bid: float | None, ask: float | None) -> float | None:
    if bid is not None and ask is not None:
        return ask - bid
    return None


def _prob_direction(prob_yes: float | None) -> str:
    if prob_yes is None:
        return "NEUTRAL"
    if prob_yes >= 0.55:
        return "UP"
    if prob_yes <= 0.45:
        return "DOWN"
    return "NEUTRAL"


def pm_context_from_snapshot_row(
    row: sqlite3.Row | None,
    timeframe: str,
    prefix: str,
) -> PolymarketTfContext:
    ctx = PolymarketTfContext(timeframe=timeframe)
    if not row:
        return ctx

    slug = row[f"market_{prefix}_slug"]
    if not slug:
        return ctx

    yb = row[f"market_{prefix}_yes_bid"]
    ya = row[f"market_{prefix}_yes_ask"]
    nb = row[f"market_{prefix}_no_bid"]
    na = row[f"market_{prefix}_no_ask"]
    mid = _mid(yb, ya)
    ctx.market_slug = slug
    ctx.yes_bid = yb
    ctx.yes_ask = ya
    ctx.no_bid = nb
    ctx.no_ask = na
    ctx.midpoint = mid
    ctx.spread = _spread(yb, ya)
    ctx.strike = row[f"market_{prefix}_strike"]
    ctx.seconds_left = row[f"market_{prefix}_seconds_left"]
    ctx.prob_yes = mid
    ctx.prob_direction = _prob_direction(mid)
    ctx.quote_ts = int(row["timestamp"]) if row["timestamp"] else None
    ctx.available = mid is not None
    return ctx


def load_pm_context_at_ts(
    conn: sqlite3.Connection,
    ts: int,
    timeframe: str,
) -> PolymarketTfContext:
    from bot.research.mtf.snapshots import snapshot_at_or_before, snapshot_nearest

    prefix_map = {"15m": "15m", "1h": "1h", "daily": "daily"}
    prefix = prefix_map.get(timeframe)
    if not prefix:
        return PolymarketTfContext(timeframe)

    row = snapshot_at_or_before(conn, ts)
    if not row or not row[f"market_{prefix}_slug"]:
        row = snapshot_nearest(conn, ts, window_sec=60)
    return pm_context_from_snapshot_row(row, timeframe, prefix)
