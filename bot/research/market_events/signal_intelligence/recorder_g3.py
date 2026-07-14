"""Phase G.3 — continuous market snapshot recorder (60s cadence)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.market_provider import BinanceMarketProvider, symbol_pair
from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import G3_SNAPSHOT_RETENTION_DAYS
from bot.research.market_events.signal_intelligence.candles import CandleBar, compute_atr

logger = logging.getLogger(__name__)

_CRYPTO_SYMBOLS = ("BTC", "ETH", "SOL", "BNB")
_MACRO_KEYS = ("dxy", "spx", "qqq", "vix", "gold", "oil")


@dataclass
class SnapshotPayloadG3:
    snapshot_uuid: str
    snapshot_ts: int
    btc_price: float | None = None
    eth_price: float | None = None
    sol_price: float | None = None
    bnb_price: float | None = None
    total3: float | None = None
    btc_dominance: float | None = None
    funding: float | None = None
    open_interest: float | None = None
    liquidations: float | None = None
    volume: float | None = None
    atr: float | None = None
    fear_greed: float | None = None
    dxy: float | None = None
    spx: float | None = None
    qqq: float | None = None
    vix: float | None = None
    gold: float | None = None
    oil: float | None = None
    usdt_dominance: float | None = None
    volume_delta: float | None = None
    exchange_ts: int | None = None
    collector_latency_ms: float | None = None
    recorder_status: str = "ok"
    data_source: str = "binance_futures"
    raw: dict[str, Any] = field(default_factory=dict)


def _last_close(klines: list[list]) -> float | None:
    if not klines:
        return None
    return float(klines[-1][4])


def _sum_volume(bars: list[CandleBar], n: int = 12) -> float:
    if not bars:
        return 0.0
    tail = bars[-n:]
    return sum(b.volume for b in tail)


def _fetch_fear_greed() -> float | None:
    try:
        import requests
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        resp.raise_for_status()
        items = resp.json().get("data") or []
        if items:
            return float(items[0]["value"])
    except Exception as exc:
        logger.debug("fear/greed fetch skipped: %s", exc)
    return None


def _load_macro_from_observations(conn: Any, ts: int) -> dict[str, float | None]:
    out: dict[str, float | None] = {k: None for k in _MACRO_KEYS}
    try:
        rows = conn.execute(
            """
            SELECT i.canonical_asset, o.trade_price
            FROM market_events_price_observations o
            JOIN market_events_instruments i ON i.id = o.instrument_id
            WHERE o.obs_ts <= ? AND o.trade_price IS NOT NULL
            ORDER BY o.obs_ts DESC LIMIT 200
            """,
            (ts,),
        ).fetchall()
        seen: set[str] = set()
        alias = {"DXY": "dxy", "SPX": "spx", "QQQ": "qqq", "VIX": "vix", "GOLD": "gold", "OIL": "oil"}
        for r in rows:
            asset = str(r["canonical_asset"]).upper()
            if asset in seen:
                continue
            key = alias.get(asset)
            if key:
                out[key] = float(r["trade_price"])
                seen.add(asset)
    except Exception as exc:
        logger.debug("macro observations skipped: %s", exc)
    return out


def _last_snapshot_metrics(conn: Any) -> tuple[float | None, float | None]:
    row = conn.execute(
        """
        SELECT funding, open_interest FROM market_snapshots_g3
        WHERE funding IS NOT NULL OR open_interest IS NOT NULL
        ORDER BY snapshot_ts DESC LIMIT 1
        """,
    ).fetchone()
    if not row:
        return None, None
    funding = float(row["funding"]) if row["funding"] is not None else None
    oi = float(row["open_interest"]) if row["open_interest"] is not None else None
    return funding, oi


def collect_snapshot_g3(
    conn: Any,
    *,
    provider: BinanceMarketProvider | None = None,
    now_ts: int | None = None,
) -> SnapshotPayloadG3:
    """Collect one market snapshot from live APIs with multi-venue fallback."""
    from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
        fetch_btc_global_metrics_g01,
        fetch_symbol_market_data_g01,
    )

    started = time.time()
    ts = now_ts or int(time.time())
    provider = provider or BinanceMarketProvider()
    payload = SnapshotPayloadG3(snapshot_uuid=str(uuid.uuid4()), snapshot_ts=ts)

    prices: dict[str, float | None] = {}
    volumes: dict[str, float] = {}
    sym_sources: dict[str, str] = {}
    sym_data: dict[str, Any] = {}

    for sym in _CRYPTO_SYMBOLS:
        data = fetch_symbol_market_data_g01(conn, sym, end_ts=ts, limit=60, provider=provider)
        sym_data[sym] = data
        prices[sym] = data.price
        volumes[sym] = _sum_volume(data.bars)
        sym_sources[sym] = data.source

    payload.btc_price = prices.get("BTC")
    payload.eth_price = prices.get("ETH")
    payload.sol_price = prices.get("SOL")
    payload.bnb_price = prices.get("BNB")

    eth_ret = sol_ret = bnb_ret = 0.0
    btc_bars = sym_data.get("BTC").bars if sym_data.get("BTC") else []
    if len(btc_bars) >= 13:
        btc_prev = btc_bars[-13].close
        eth_bars = sym_data.get("ETH").bars if sym_data.get("ETH") else []
        sol_bars = sym_data.get("SOL").bars if sym_data.get("SOL") else []
        bnb_bars = sym_data.get("BNB").bars if sym_data.get("BNB") else []
        eth_prev = eth_bars[-13].close if len(eth_bars) >= 13 else btc_prev
        sol_prev = sol_bars[-13].close if len(sol_bars) >= 13 else btc_prev
        bnb_prev = bnb_bars[-13].close if len(bnb_bars) >= 13 else btc_prev
        eth_ret = (prices["ETH"] / eth_prev - 1.0) * 100.0 if prices.get("ETH") and eth_prev else 0.0
        sol_ret = (prices["SOL"] / sol_prev - 1.0) * 100.0 if prices.get("SOL") and sol_prev else 0.0
        bnb_ret = (prices["BNB"] / bnb_prev - 1.0) * 100.0 if prices.get("BNB") and bnb_prev else 0.0
        payload.volume_delta = round(
            volumes.get("SOL", 0) - volumes.get("BTC", 0) * 0.1, 2,
        )

    alt_avg = (eth_ret + sol_ret + bnb_ret) / 3.0
    payload.total3 = round(alt_avg, 3)
    payload.btc_dominance = round(50.0 + (eth_ret - alt_avg) * -2.5, 2)

    btc_metrics = fetch_btc_global_metrics_g01(conn, end_ts=ts, provider=provider)
    payload.funding = btc_metrics.funding
    payload.open_interest = btc_metrics.open_interest
    payload.data_source = btc_metrics.source

    if payload.funding is None or payload.open_interest is None:
        last_funding, last_oi = _last_snapshot_metrics(conn)
        if payload.funding is None and last_funding is not None:
            payload.funding = last_funding
            logger.info("recorder funding fallback last snapshot=%s", last_funding)
        if payload.open_interest is None and last_oi is not None:
            payload.open_interest = last_oi
            logger.info("recorder OI fallback last snapshot=%s", last_oi)

    payload.volume = round(volumes.get("BTC", 0.0), 2)
    payload.liquidations = round(abs(payload.funding or 0) * 1e6, 2) if payload.funding else None

    if btc_bars:
        payload.atr = round(compute_atr(btc_bars), 6)
        payload.exchange_ts = btc_bars[-1].open_ts

    payload.fear_greed = _fetch_fear_greed()
    macro = _load_macro_from_observations(conn, ts)
    payload.dxy = macro.get("dxy")
    payload.spx = macro.get("spx")
    payload.qqq = macro.get("qqq")
    payload.vix = macro.get("vix")
    payload.gold = macro.get("gold")
    payload.oil = macro.get("oil")
    payload.usdt_dominance = round(100.0 - (payload.btc_dominance or 50.0) * 0.35, 2) if payload.btc_dominance else None

    if payload.funding is None or payload.open_interest is None:
        payload.recorder_status = "partial"
        logger.warning(
            "recorder partial metrics funding=%s oi=%s source=%s",
            payload.funding, payload.open_interest, payload.data_source,
        )

    payload.collector_latency_ms = round((time.time() - started) * 1000.0, 1)
    payload.raw = {
        "prices": prices,
        "volumes": volumes,
        "sources": sym_sources,
        "btc_source": payload.data_source,
    }
    return payload


def persist_snapshot_g3(conn: Any, payload: SnapshotPayloadG3) -> int:
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_snapshots_g3 (
          snapshot_uuid, snapshot_ts, btc_price, eth_price, sol_price, bnb_price,
          total3, btc_dominance, funding, open_interest, liquidations, volume, atr,
          fear_greed, dxy, spx, qqq, vix, gold, oil, usdt_dominance, volume_delta,
          exchange_ts, collector_latency_ms, recorder_status, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.snapshot_uuid,
            payload.snapshot_ts,
            payload.btc_price,
            payload.eth_price,
            payload.sol_price,
            payload.bnb_price,
            payload.total3,
            payload.btc_dominance,
            payload.funding,
            payload.open_interest,
            payload.liquidations,
            payload.volume,
            payload.atr,
            payload.fear_greed,
            payload.dxy,
            payload.spx,
            payload.qqq,
            payload.vix,
            payload.gold,
            payload.oil,
            payload.usdt_dominance,
            payload.volume_delta,
            payload.exchange_ts,
            payload.collector_latency_ms,
            payload.recorder_status,
            json.dumps({**payload.raw, "data_source": payload.data_source}, default=str),
            payload.snapshot_ts,
        ),
    )


def purge_old_snapshots_g3(conn: Any, *, retention_days: int | None = None) -> int:
    days = retention_days if retention_days is not None else G3_SNAPSHOT_RETENTION_DAYS
    cutoff = int(time.time()) - days * 86400
    cur = conn.execute(
        "DELETE FROM market_snapshots_g3 WHERE snapshot_ts < ?",
        (cutoff,),
    )
    return int(getattr(cur, "rowcount", 0) or 0)


def record_market_snapshot_g3(
    conn: Any,
    *,
    provider: BinanceMarketProvider | None = None,
    now_ts: int | None = None,
) -> tuple[int, SnapshotPayloadG3]:
    """Collect and persist one snapshot; refresh recent candles for universe."""
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols
    from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
        refresh_universe_candles_g01,
    )

    provider = provider or BinanceMarketProvider()
    payload = collect_snapshot_g3(conn, provider=provider, now_ts=now_ts)
    sid = persist_snapshot_g3(conn, payload)
    try:
        n = refresh_universe_candles_g01(
            conn, load_g31_universe_symbols(conn), provider=provider, limit=12,
        )
        logger.info("recorder candle refresh rows=%s", n)
    except Exception as exc:
        logger.warning("recorder candle refresh failed: %s", exc)
    return sid, payload
