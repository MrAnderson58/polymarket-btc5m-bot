"""Phase F.2 Task A — multi-exchange move consensus (Bybit, Binance, OKX)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.config import RESOLVER_VENUES

CONSENSUS_STRONG = "STRONG"
CONSENSUS_PARTIAL = "PARTIAL"
CONSENSUS_WEAK = "WEAK"

VALID_CONSENSUS = frozenset({CONSENSUS_STRONG, CONSENSUS_PARTIAL, CONSENSUS_WEAK})

CONFIDENCE_ADJ = {
    CONSENSUS_STRONG: 0.4,
    CONSENSUS_PARTIAL: 0.0,
    CONSENSUS_WEAK: -0.6,
}


@dataclass(frozen=True)
class ExchangeConsensusResult:
    consensus: str
    confirmed_count: int
    venues: list[dict[str, Any]]


def _window_return_from_candles(
    conn: Any,
    *,
    symbol: str,
    venue: str,
    window_seconds: int,
) -> float | None:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=24)
    if len(bars) < 2:
        return None
    n = max(1, window_seconds // 300)
    if len(bars) <= n:
        return None
    start = bars[-n - 1].close
    end = bars[-1].close
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _fetch_ticker_move(symbol: str, venue: str) -> float | None:
    try:
        if venue == "bybit":
            from bot.research.market_events.venue_bybit import BybitMarketClient
            ticker = BybitMarketClient().fetch_ticker(f"{symbol}USDT")
            if ticker and ticker.last_price:
                return float(getattr(ticker, "price_change_pct", 0) or 0)
        elif venue == "binance":
            import requests
            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/24hr",
                params={"symbol": f"{symbol}USDT"},
                timeout=6,
            )
            if resp.ok:
                return float(resp.json().get("priceChangePercent", 0) or 0)
        elif venue == "okx":
            from bot.research.market_events.signal_intelligence.okx_client import fetch_ticker_metrics
            metrics = fetch_ticker_metrics(symbol)
            if metrics:
                return float(metrics.get("price_change_pct", 0) or 0)
    except Exception:
        return None
    return None


def _venue_confirmed(move_pct: float | None, *, shock_ret: float, min_ratio: float = 0.35) -> bool:
    if move_pct is None or shock_ret == 0:
        return False
    same_sign = (move_pct >= 0) == (shock_ret >= 0)
    if not same_sign:
        return False
    threshold = max(0.8, abs(shock_ret) * min_ratio)
    return abs(move_pct) >= threshold


def compute_exchange_consensus(
    conn: Any,
    *,
    symbol: str,
    shock_return_pct: float,
    window_seconds: int = 900,
) -> ExchangeConsensusResult:
    venues_out: list[dict[str, Any]] = []
    confirmed = 0

    for venue in RESOLVER_VENUES:
        venue_key = (
            "binance_futures" if venue == "binance"
            else "bybit_linear" if venue == "bybit"
            else "okx"
        )
        move = _window_return_from_candles(
            conn, symbol=symbol, venue=venue_key, window_seconds=window_seconds,
        )
        source = "candles"
        if move is None:
            move = _fetch_ticker_move(symbol, venue)
            source = "ticker"
        ok = _venue_confirmed(move, shock_ret=shock_return_pct)
        if ok:
            confirmed += 1
        venues_out.append({
            "venue": venue,
            "move_pct": round(move, 3) if move is not None else None,
            "confirmed": ok,
            "source": source if move is not None else "unavailable",
        })

    if confirmed >= 3:
        consensus = CONSENSUS_STRONG
    elif confirmed == 2:
        consensus = CONSENSUS_PARTIAL
    else:
        consensus = CONSENSUS_WEAK

    return ExchangeConsensusResult(
        consensus=consensus,
        confirmed_count=confirmed,
        venues=venues_out,
    )


def persist_exchange_contexts(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    consensus: ExchangeConsensusResult,
) -> None:
    """Store per-venue snapshot rows for research (additive to F.0 context)."""
    now = int(time.time())
    for v in consensus.venues:
        venue = v["venue"]
        venue_key = (
            "bybit_linear" if venue == "bybit"
            else "binance_futures" if venue == "binance"
            else "okx"
        )
        sym = f"{symbol}USDT" if venue != "okx" else f"{symbol}-USDT-SWAP"
        raw = json.dumps({"f2_consensus": v, "move_pct": v.get("move_pct")})
        conn.execute(
            """
            INSERT INTO market_event_exchange_context (
              event_id, venue, symbol, funding, open_interest, long_short_ratio,
              volume_24h, spread_bps, vwap, atr, ema20_distance_pct, ema50_distance_pct,
              raw_json, created_at
            ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
            ON CONFLICT(event_id, venue) DO UPDATE SET
              raw_json = excluded.raw_json,
              created_at = excluded.created_at
            """,
            (event_id, venue_key, sym, raw, now),
        )
