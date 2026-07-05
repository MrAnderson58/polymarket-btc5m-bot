"""Audited HTF strike resolution — research/collector only.

Source priority (first match wins):
  A. Gamma market metadata (structured window times)
  B. Event metadata
  C/D. Question/description parsing (determines required reference source)
  E. Binance open/close when rules explicitly require Binance
  F. Chainlink when rules require Chainlink (optional API credentials)

Daily markets compare two noon ET closes — no single window strike.
We store the prior-day noon reference close when rules are parseable.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from bot.config import BINANCE_API, BTC_SYMBOL, GAMMA_API
from bot.research.mtf.config import SLUG_15M_PREFIX, TF_15M_SECONDS
from bot.research.mtf.discovery import (
    ET,
    _fetch_gamma_event,
    parse_15m_window_start_ts,
    slug_1h_at,
)
from bot.research.mtf.metadata import MarketMetadata, get_cached_metadata, upsert_metadata

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 5

# Allowed strike source labels
GAMMA_METADATA = "GAMMA_METADATA"
EVENT_METADATA = "EVENT_METADATA"
QUESTION_PARSE = "QUESTION_PARSE"
DESCRIPTION_PARSE = "DESCRIPTION_PARSE"
BINANCE_OPEN = "BINANCE_OPEN"
CHAINLINK_REFERENCE = "CHAINLINK_REFERENCE"
UNKNOWN = "UNKNOWN"

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


@dataclass
class StrikeResult:
    strike: float | None
    strike_source: str
    strike_source_timestamp: int | None
    strike_confidence: float
    market_slug: str
    window_start_ts: int | None = None
    window_end_ts: int | None = None
    resolution_notes: str = ""


def _parse_iso_ts(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def _fetch_gamma_event_local(slug: str) -> dict[str, Any] | None:
    return _fetch_gamma_event(slug)


def _gamma_market(event: dict[str, Any]) -> dict[str, Any] | None:
    markets = event.get("markets") or []
    return markets[0] if markets else None


def _detect_resolution_source(description: str, resolution_source: str) -> str:
    text = f"{description} {resolution_source}".lower()
    if "chainlink" in text:
        return "chainlink"
    if "binance" in text:
        return "binance"
    return "unknown"


def _fetch_binance_kline_open(interval: str, start_ts: int) -> tuple[float, int] | None:
    try:
        resp = requests.get(
            f"{BINANCE_API}/api/v3/klines",
            params={
                "symbol": BTC_SYMBOL,
                "interval": interval,
                "startTime": start_ts * 1000,
                "limit": 1,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        candles = resp.json()
        if not candles:
            return None
        open_price = float(candles[0][1])
        open_ts = int(candles[0][0] // 1000)
        if open_price <= 0:
            return None
        return open_price, open_ts
    except Exception as exc:
        logger.warning("Binance kline fetch failed interval=%s start=%s: %s", interval, start_ts, exc)
        return None


def _fetch_binance_1m_close_at(ts: int) -> tuple[float, int] | None:
    try:
        resp = requests.get(
            f"{BINANCE_API}/api/v3/klines",
            params={
                "symbol": BTC_SYMBOL,
                "interval": "1m",
                "startTime": ts * 1000,
                "limit": 1,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        candles = resp.json()
        if not candles:
            return None
        close_price = float(candles[0][4])
        close_ts = int(candles[0][0] // 1000)
        if close_price <= 0:
            return None
        return close_price, close_ts
    except Exception as exc:
        logger.warning("Binance 1m close fetch failed ts=%s: %s", ts, exc)
        return None


def _fetch_chainlink_price_at(ts: int) -> tuple[float, int] | None:
    """Optional Chainlink Data Streams fetch (requires env credentials)."""
    api_key = os.getenv("CHAINLINK_STREAMS_API_KEY")
    api_secret = os.getenv("CHAINLINK_STREAMS_API_SECRET")
    feed_id = os.getenv(
        "CHAINLINK_BTC_USD_FEED_ID",
        "0x0003735a076086936550bd316b18e5e27fc4f280ee5b6530ce68f5aad404c796",
    )
    if not api_key or not api_secret:
        return None
    try:
        import hashlib
        import hmac

        path = f"/api/v1/reports?feedID={feed_id}&timestamp={ts}"
        url = f"https://api.dataengine.chain.link{path}"
        ts_ms = str(int(time.time() * 1000))
        body_hash = hashlib.sha256(b"").hexdigest()
        msg = f"GET{path}{body_hash}{api_key}{ts_ms}"
        sig = hmac.new(api_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        resp = requests.get(
            url,
            headers={
                "Authorization": api_key,
                "X-Authorization-Timestamp": ts_ms,
                "X-Authorization-Signature-SHA256": sig,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        report = resp.json().get("report") or {}
        # Price decoding depends on report schema; store raw if parse fails
        full = report.get("fullReport")
        if not full:
            return None
        # Without full decoder, return None — credentials present but decode not implemented
        logger.debug("Chainlink report received but price decode not implemented")
        return None
    except Exception as exc:
        logger.warning("Chainlink fetch failed ts=%s: %s", ts, exc)
        return None


def _parse_1h_window_start_from_slug(slug: str) -> int | None:
    m = re.match(
        r"^bitcoin-up-or-down-([a-z]+)-(\d+)-(\d+)-(\d{1,2}(?:am|pm))-et$",
        slug,
    )
    if not m:
        return None
    month_name, day, year, hour_label = m.groups()
    month = _MONTHS.get(month_name.lower())
    if not month:
        return None
    hour = _parse_hour_label(hour_label)
    if hour is None:
        return None
    dt = datetime(int(year), month, int(day), hour, 0, tzinfo=ET)
    return int(dt.timestamp())


def _parse_hour_label(label: str) -> int | None:
    label = label.lower()
    m = re.match(r"^(\d{1,2})(am|pm)$", label)
    if not m:
        return None
    h = int(m.group(1))
    if m.group(2) == "am":
        return 0 if h == 12 else h
    return 12 if h == 12 else h + 12


def _parse_daily_reference_noon(slug: str, description: str) -> tuple[int, str] | None:
    """Return prior-day noon ET timestamp for daily comparison markets."""
    m = re.match(r"^bitcoin-up-or-down-on-([a-z]+)-(\d+)-(\d+)$", slug)
    if not m:
        return None
    month_name, day, year = m.groups()
    month = _MONTHS.get(month_name.lower())
    if not month:
        return None
    market_day = datetime(int(year), month, int(day), 12, 0, tzinfo=ET)
    # Description references prior day noon close as baseline
    prior_noon = market_day - timedelta(days=1)
    return int(prior_noon.timestamp()), (
        "Daily market compares noon ET closes; stored value is prior-day noon reference close."
    )


def _resolve_15m_strike(
    slug: str,
    event: dict[str, Any],
    market: dict[str, Any],
    snapshot_ts: int,
) -> StrikeResult:
    ws = parse_15m_window_start_ts(slug)
    end_ts = ws + TF_15M_SECONDS if ws else _parse_iso_ts(market.get("endDate") or event.get("endDate"))
    event_start = _parse_iso_ts(market.get("eventStartTime"))
    if ws is None and event_start is not None:
        ws = event_start

    desc = market.get("description") or event.get("description") or ""
    res_src = market.get("resolutionSource") or event.get("resolutionSource") or ""
    source_kind = _detect_resolution_source(desc, res_src)

    if ws is None:
        return StrikeResult(None, UNKNOWN, None, 0.0, slug, None, end_ts, "cannot parse 15m window")

    if snapshot_ts < ws:
        return StrikeResult(
            None, UNKNOWN, None, 0.0, slug, ws, end_ts,
            "strike not fixed until window start (Chainlink at eventStartTime)",
        )

    if source_kind == "chainlink":
        cl = _fetch_chainlink_price_at(ws)
        if cl:
            price, src_ts = cl
            return StrikeResult(price, CHAINLINK_REFERENCE, src_ts, 0.95, slug, ws, end_ts)
        return StrikeResult(
            None, UNKNOWN, ws, 0.0, slug, ws, end_ts,
            "Chainlink required by rules; set CHAINLINK_STREAMS_API_KEY or wait for manual enrichment",
        )

    return StrikeResult(None, UNKNOWN, ws, 0.0, slug, ws, end_ts, f"unsupported resolution source: {source_kind}")


def _resolve_1h_strike(
    slug: str,
    event: dict[str, Any],
    market: dict[str, Any],
    snapshot_ts: int,
) -> StrikeResult:
    ws = _parse_1h_window_start_from_slug(slug)
    end_ts = _parse_iso_ts(market.get("endDate") or event.get("endDate"))
    desc = market.get("description") or event.get("description") or ""
    res_src = market.get("resolutionSource") or event.get("resolutionSource") or ""
    source_kind = _detect_resolution_source(desc, res_src)

    if ws is None:
        return StrikeResult(None, UNKNOWN, None, 0.0, slug, None, end_ts, "cannot parse 1h window from slug")

    if snapshot_ts < ws:
        return StrikeResult(
            None, UNKNOWN, None, 0.0, slug, ws, end_ts,
            "1h strike (Binance open) not fixed until candle start",
        )

    if source_kind != "binance":
        return StrikeResult(None, UNKNOWN, ws, 0.0, slug, ws, end_ts, "1h rules require Binance; source mismatch")

    kline = _fetch_binance_kline_open("1h", ws)
    if kline:
        price, src_ts = kline
        if src_ts <= snapshot_ts:
            return StrikeResult(price, BINANCE_OPEN, src_ts, 0.92, slug, ws, end_ts)
        return StrikeResult(
            None, UNKNOWN, None, 0.0, slug, ws, end_ts,
            "Binance 1h open timestamp after snapshot (no look-ahead)",
        )

    return StrikeResult(None, UNKNOWN, ws, 0.0, slug, ws, end_ts, "Binance 1h open unavailable")


def _resolve_daily_strike(
    slug: str,
    event: dict[str, Any],
    market: dict[str, Any],
    snapshot_ts: int,
) -> StrikeResult:
    end_ts = _parse_iso_ts(market.get("endDate") or event.get("endDate"))
    desc = market.get("description") or event.get("description") or ""
    parsed = _parse_daily_reference_noon(slug, desc)
    if not parsed:
        return StrikeResult(
            None, UNKNOWN, None, end_ts, slug, None, end_ts,
            "daily market has no single strike; comparison of two noon closes",
        )
    ref_ts, note = parsed
    if snapshot_ts < ref_ts:
        return StrikeResult(
            None, UNKNOWN, None, 0.0, slug, ref_ts, end_ts,
            "prior-day noon reference not yet fixed at snapshot time",
        )
    kline = _fetch_binance_1m_close_at(ref_ts)
    if kline:
        price, src_ts = kline
        if src_ts <= snapshot_ts:
            return StrikeResult(
                price, DESCRIPTION_PARSE, src_ts, 0.75, slug, ref_ts, end_ts,
                note,
            )
    return StrikeResult(
        None, UNKNOWN, ref_ts, 0.0, slug, ref_ts, end_ts,
        "prior-day noon Binance close unavailable",
    )


def resolve_strike(
    slug: str,
    timeframe: str,
    *,
    snapshot_ts: int | None = None,
    event: dict[str, Any] | None = None,
) -> StrikeResult:
    """Resolve strike for a market slug at snapshot_ts (no look-ahead)."""
    snapshot_ts = snapshot_ts or int(time.time())
    if event is None:
        event = _fetch_gamma_event_local(slug)
    if not event:
        return StrikeResult(None, UNKNOWN, None, 0.0, slug, resolution_notes="gamma event not found")

    market = _gamma_market(event)
    if not market:
        return StrikeResult(None, UNKNOWN, None, 0.0, slug, resolution_notes="gamma market missing")

    # A/B: structured window metadata from Gamma
    gamma_ws = _parse_iso_ts(market.get("eventStartTime"))
    gamma_end = _parse_iso_ts(market.get("endDate") or event.get("endDate"))

    if timeframe == "15m":
        result = _resolve_15m_strike(slug, event, market, snapshot_ts)
    elif timeframe == "1h":
        result = _resolve_1h_strike(slug, event, market, snapshot_ts)
    elif timeframe == "daily":
        result = _resolve_daily_strike(slug, event, market, snapshot_ts)
    else:
        result = StrikeResult(None, UNKNOWN, None, 0.0, slug)

    if result.window_start_ts is None and gamma_ws is not None:
        result.window_start_ts = gamma_ws
        if result.strike_source == UNKNOWN and result.strike is None:
            result.resolution_notes += " | window from GAMMA_METADATA eventStartTime"
    if result.window_end_ts is None and gamma_end is not None:
        result.window_end_ts = gamma_end

    return result


def get_or_resolve_strike(
    conn: sqlite3.Connection,
    slug: str,
    timeframe: str,
    *,
    snapshot_ts: int | None = None,
    event: dict[str, Any] | None = None,
) -> StrikeResult:
    """Return cached immutable strike or resolve and cache."""
    from bot.research.mtf.metadata import ensure_metadata_table

    ensure_metadata_table(conn)
    snapshot_ts = snapshot_ts or int(time.time())

    cached = get_cached_metadata(conn, slug)
    if cached and cached.strike is not None and cached.strike_confidence >= 0.7:
        return StrikeResult(
            cached.strike,
            cached.strike_source,
            cached.strike_source_timestamp,
            cached.strike_confidence,
            slug,
            cached.window_start_ts,
            cached.window_end_ts,
            "cached immutable metadata",
        )

    result = resolve_strike(slug, timeframe, snapshot_ts=snapshot_ts, event=event)
    upsert_metadata(
        conn,
        MarketMetadata(
            market_slug=slug,
            timeframe=timeframe,
            window_start_ts=result.window_start_ts,
            window_end_ts=result.window_end_ts,
            strike=result.strike,
            strike_source=result.strike_source,
            strike_source_timestamp=result.strike_source_timestamp,
            strike_confidence=result.strike_confidence,
            discovered_at=snapshot_ts,
            raw_metadata_json={"resolution_notes": result.resolution_notes},
        ),
    )
    return result


def backfill_snapshot_strikes(conn: sqlite3.Connection) -> dict[str, int]:
    """Backfill snapshot strike columns from immutable metadata cache.

    Look-ahead safe: only applies cached strikes whose source timestamp
    is <= snapshot timestamp and strike was fixed at market open per rules.
    """
    from bot.research.mtf.snapshots import TABLE, ensure_tables

    ensure_tables(conn)
    from bot.research.mtf.metadata import ensure_metadata_table

    ensure_metadata_table(conn)
    stats = {"updated": 0, "skipped": 0}

    rows = conn.execute(
        f"""
        SELECT id, timestamp,
               market_15m_slug, market_15m_strike,
               market_1h_slug, market_1h_strike,
               market_daily_slug, market_daily_strike
        FROM {TABLE}
        """
    ).fetchall()

    tf_map = [
        ("15m", "market_15m_slug", "market_15m_strike"),
        ("1h", "market_1h_slug", "market_1h_strike"),
        ("daily", "market_daily_slug", "market_daily_strike"),
    ]

    for row in rows:
        snap_ts = int(row["timestamp"])
        updates: dict[str, float] = {}
        for _tf, slug_col, strike_col in tf_map:
            slug = row[slug_col]
            if not slug or row[strike_col] is not None:
                continue
            meta = get_cached_metadata(conn, slug)
            if not meta or meta.strike is None:
                stats["skipped"] += 1
                continue
            src_ts = meta.strike_source_timestamp or meta.window_start_ts
            if src_ts is not None and src_ts > snap_ts:
                stats["skipped"] += 1
                continue
            if meta.strike_source == UNKNOWN:
                stats["skipped"] += 1
                continue
            updates[strike_col] = meta.strike

        if updates:
            set_clause = ", ".join(f"{col} = ?" for col in updates)
            conn.execute(
                f"UPDATE {TABLE} SET {set_clause} WHERE id = ?",
                (*updates.values(), row["id"]),
            )
            stats["updated"] += 1

    return stats
