"""Exchange context capture at shock time — funding, OI, spread, VWAP, ATR, EMA."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import (
    compute_atr,
    compute_ema,
    compute_vwap,
    load_recent_candles,
    pct_distance,
)
from bot.research.market_events.signal_intelligence.exchange_resolver import get_or_resolve


def _fetch_bybit_context(symbol: str) -> dict[str, Any]:
    from bot.research.market_events.venue_bybit import BybitMarketClient
    client = BybitMarketClient()
    ticker = client.fetch_ticker(symbol)
    if not ticker:
        return {}
    spread_bps = None
    if ticker.bid and ticker.ask and ticker.ask > 0:
        spread_bps = (ticker.ask - ticker.bid) / ticker.ask * 10000.0
    return {
        "funding": None,
        "open_interest": None,
        "long_short_ratio": None,
        "volume_24h": ticker.volume_24h,
        "spread_bps": spread_bps,
        "last_price": ticker.last_price,
    }


def _fetch_binance_context(symbol: str) -> dict[str, Any]:
    import requests
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=8,
        )
        prem = resp.json() if resp.ok else {}
        funding = float(prem.get("lastFundingRate", 0) or 0) * 100.0
        ticker = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            params={"symbol": symbol},
            timeout=8,
        ).json()
        vol = float(ticker.get("volume", 0) or 0)
        return {"funding": funding, "volume_24h": vol, "last_price": float(ticker.get("lastPrice", 0) or 0)}
    except Exception:
        return {}


def fetch_exchange_metrics(canonical: str, venue: str, symbol: str) -> dict[str, Any]:
    if venue == "bybit":
        return _fetch_bybit_context(symbol)
    if venue == "binance":
        return _fetch_binance_context(symbol)
    if venue == "okx":
        from bot.research.market_events.signal_intelligence.okx_client import fetch_ticker_metrics
        return fetch_ticker_metrics(canonical) or {}
    return {}


def capture_exchange_context(conn: Any, *, event_id: int, symbol: str) -> dict[str, Any] | None:
    resolution = get_or_resolve(conn, symbol)
    if resolution.status != "RESOLVED" or not resolution.resolved_venue:
        return None

    venue = resolution.resolved_venue
    venue_symbol = resolution.resolved_symbol or symbol
    metrics = fetch_exchange_metrics(symbol, venue, venue_symbol)

    venue_key = "bybit_linear" if venue == "bybit" else "binance_futures" if venue == "binance" else "okx"
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=60)
    vwap = atr = ema20_dist = ema50_dist = None
    if bars:
        closes = [b.close for b in bars]
        vwap = compute_vwap(bars[-20:])
        atr = compute_atr(bars, 14)
        price = closes[-1]
        ema20_dist = pct_distance(price, compute_ema(closes[-20:], 20))
        ema50_dist = pct_distance(price, compute_ema(closes, min(50, len(closes))))

    now = int(time.time())
    raw = {"resolution": resolution.attempts, "metrics": metrics}
    insert_returning_id(
        conn,
        """
        INSERT INTO market_event_exchange_context (
          event_id, venue, symbol, funding, open_interest, long_short_ratio,
          volume_24h, spread_bps, vwap, atr, ema20_distance_pct, ema50_distance_pct,
          raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id, venue) DO UPDATE SET
          symbol = excluded.symbol,
          funding = excluded.funding,
          open_interest = excluded.open_interest,
          long_short_ratio = excluded.long_short_ratio,
          volume_24h = excluded.volume_24h,
          spread_bps = excluded.spread_bps,
          vwap = excluded.vwap,
          atr = excluded.atr,
          ema20_distance_pct = excluded.ema20_distance_pct,
          ema50_distance_pct = excluded.ema50_distance_pct,
          raw_json = excluded.raw_json,
          created_at = excluded.created_at
        """,
        (
            event_id, venue, venue_symbol,
            metrics.get("funding"), metrics.get("open_interest"),
            metrics.get("long_short_ratio"), metrics.get("volume_24h"),
            metrics.get("spread_bps"), vwap, atr, ema20_dist, ema50_dist,
            json.dumps(raw), now,
        ),
    )
    return {
        "event_id": event_id,
        "venue": venue,
        "symbol": venue_symbol,
        "funding": metrics.get("funding"),
        "vwap": vwap,
        "atr": atr,
    }


def exchange_context_report(conn: Any, *, days: int = 7) -> str:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        """
        SELECT e.symbol, c.venue, c.funding, c.spread_bps, c.vwap, c.created_at
        FROM market_event_exchange_context c
        JOIN market_events e ON e.id = c.event_id
        WHERE c.created_at >= ?
        ORDER BY c.created_at DESC LIMIT 20
        """,
        (since,),
    ).fetchall()
    lines = ["EXCHANGE CONTEXT REPORT (F.0)", ""]
    for r in rows:
        lines.append(
            f"  {r['symbol']} venue={r['venue']} funding={r['funding']} spread={r['spread_bps']}",
        )
    if not rows:
        lines.append("  (no context rows)")
    return "\n".join(lines)
