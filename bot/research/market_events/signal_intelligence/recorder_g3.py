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
    raw: dict[str, Any] = field(default_factory=dict)


def _last_close(klines: list[list]) -> float | None:
    if not klines:
        return None
    return float(klines[-1][4])


def _sum_volume(klines: list[list], n: int = 12) -> float:
    if not klines:
        return 0.0
    tail = klines[-n:]
    return sum(float(k[5]) for k in tail)


def _klines_to_bars(klines: list[list]) -> list[CandleBar]:
    return [
        CandleBar(
            open_ts=int(k[0] // 1000),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )
        for k in klines
    ]


def _fetch_open_interest(provider: BinanceMarketProvider, pair: str) -> float | None:
    data = provider._get(  # noqa: SLF001 — reuse session/retry
        f"{provider.futures_api}/fapi/v1/openInterest",
        {"symbol": pair},
    )
    if not data:
        return None
    try:
        return float(data["openInterest"])
    except (KeyError, TypeError, ValueError):
        return None


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


def collect_snapshot_g3(
    conn: Any,
    *,
    provider: BinanceMarketProvider | None = None,
    now_ts: int | None = None,
) -> SnapshotPayloadG3:
    """Collect one market snapshot from live APIs."""
    started = time.time()
    ts = now_ts or int(time.time())
    provider = provider or BinanceMarketProvider()
    payload = SnapshotPayloadG3(snapshot_uuid=str(uuid.uuid4()), snapshot_ts=ts)

    prices: dict[str, float | None] = {}
    volumes: dict[str, float] = {}
    kline_map: dict[str, list[list]] = {}

    for sym in _CRYPTO_SYMBOLS:
        pair = symbol_pair(sym)
        kl = provider.fetch_futures_klines(pair, "5m", ts, limit=60)
        kline_map[sym] = kl
        prices[sym] = _last_close(kl)
        volumes[sym] = _sum_volume(kl)

    payload.btc_price = prices.get("BTC")
    payload.eth_price = prices.get("ETH")
    payload.sol_price = prices.get("SOL")
    payload.bnb_price = prices.get("BNB")

    eth_ret = sol_ret = bnb_ret = 0.0
    btc_kl = kline_map.get("BTC") or []
    if len(btc_kl) >= 13 and btc_kl[-13][4]:
        btc_prev = float(btc_kl[-13][4])
        eth_prev = float((kline_map.get("ETH") or btc_kl)[-13][4]) if kline_map.get("ETH") else btc_prev
        sol_prev = float((kline_map.get("SOL") or btc_kl)[-13][4]) if kline_map.get("SOL") else btc_prev
        bnb_prev = float((kline_map.get("BNB") or btc_kl)[-13][4]) if kline_map.get("BNB") else btc_prev
        eth_ret = (prices["ETH"] / eth_prev - 1.0) * 100.0 if prices.get("ETH") and eth_prev else 0.0
        sol_ret = (prices["SOL"] / sol_prev - 1.0) * 100.0 if prices.get("SOL") and sol_prev else 0.0
        bnb_ret = (prices["BNB"] / bnb_prev - 1.0) * 100.0 if prices.get("BNB") and bnb_prev else 0.0
        payload.volume_delta = round(
            volumes.get("SOL", 0) - volumes.get("BTC", 0) * 0.1, 2,
        )

    alt_avg = (eth_ret + sol_ret + bnb_ret) / 3.0
    payload.total3 = round(alt_avg, 3)
    payload.btc_dominance = round(50.0 + (eth_ret - alt_avg) * -2.5, 2)

    pair_btc = symbol_pair("BTC")
    funding, _ = provider.fetch_funding_rate(pair_btc, ts)
    payload.funding = funding
    payload.open_interest = _fetch_open_interest(provider, pair_btc)
    payload.volume = round(volumes.get("BTC", 0.0), 2)
    payload.liquidations = round(abs(payload.funding or 0) * 1e6, 2) if payload.funding else None

    bars = _klines_to_bars(btc_kl)
    if bars:
        payload.atr = round(compute_atr(bars), 6)

    payload.fear_greed = _fetch_fear_greed()
    macro = _load_macro_from_observations(conn, ts)
    payload.dxy = macro.get("dxy")
    payload.spx = macro.get("spx")
    payload.qqq = macro.get("qqq")
    payload.vix = macro.get("vix")
    payload.gold = macro.get("gold")
    payload.oil = macro.get("oil")
    payload.usdt_dominance = round(100.0 - (payload.btc_dominance or 50.0) * 0.35, 2) if payload.btc_dominance else None

    if btc_kl:
        payload.exchange_ts = int(btc_kl[-1][0] // 1000)
    payload.collector_latency_ms = round((time.time() - started) * 1000.0, 1)
    payload.raw = {"prices": prices, "volumes": volumes}
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
            json.dumps(payload.raw, default=str),
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
    """Collect and persist one snapshot; returns (snapshot_id, payload)."""
    payload = collect_snapshot_g3(conn, provider=provider, now_ts=now_ts)
    sid = persist_snapshot_g3(conn, payload)
    return sid, payload
