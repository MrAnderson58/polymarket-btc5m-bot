"""Market snapshot features at signal time T — strict no look-ahead."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

import requests

from bot.research.futures.config import BINANCE_FUTURES_API, BINANCE_SPOT_API, RETURN_WINDOWS
from bot.research.futures.schema import SIGNALS_TABLE, SNAPSHOTS_TABLE

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT = 8


def _symbol_pair(symbol: str) -> str:
    s = symbol.upper().replace("/", "")
    return s if s.endswith("USDT") else f"{s}USDT"


def _fetch_klines(base: str, pair: str, interval: str, end_ts: int, limit: int = 200) -> list[list]:
    try:
        resp = requests.get(
            f"{base}/api/v3/klines" if "api.binance.com" in base else f"{base}/fapi/v1/klines",
            params={"symbol": pair, "interval": interval, "endTime": end_ts * 1000, "limit": limit},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as exc:
        logger.debug("Kline fetch failed %s %s: %s", pair, interval, exc)
        return []


def _candles_at_or_before(candles: list[list], ts: int) -> list[list]:
    return [c for c in candles if int(c[0] // 1000) <= ts]


def _close_at(candles: list[list], ts: int) -> float | None:
    eligible = _candles_at_or_before(candles, ts)
    if not eligible:
        return None
    return float(eligible[-1][4])


def _return_over(candles: list[list], ts: int, window_sec: int) -> float | None:
    p_now = _close_at(candles, ts)
    p_then = _close_at(candles, ts - window_sec)
    if p_now is None or p_then is None or p_then == 0:
        return None
    return (p_now - p_then) / p_then * 100.0


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _atr(candles: list[list], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = float(candles[i][2]), float(candles[i][3]), float(candles[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _fetch_funding(pair: str, ts: int) -> tuple[float | None, str]:
    try:
        resp = requests.get(
            f"{BINANCE_FUTURES_API}/fapi/v1/fundingRate",
            params={"symbol": pair, "endTime": ts * 1000, "limit": 1},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json() or []
        if not data:
            return None, "unavailable"
        fr_ts = int(data[0]["fundingTime"] // 1000)
        if fr_ts > ts:
            return None, "future_funding"
        return float(data[0]["fundingRate"]), "available"
    except Exception:
        return None, "unavailable"


def build_market_snapshot(symbol: str, ts: int) -> tuple[dict[str, Any], dict[str, str]]:
    """Build feature dict using only data with timestamp <= ts."""
    pair = _symbol_pair(symbol)
    coverage: dict[str, str] = {}
    features: dict[str, Any] = {"snapshot_ts": ts, "symbol": symbol, "pair": pair}

    spot_1m = _fetch_klines(BINANCE_SPOT_API, pair, "1m", ts, limit=1500)
    fut_1m = _fetch_klines(BINANCE_FUTURES_API, pair, "1m", ts, limit=1500)
    btc_1m = _fetch_klines(BINANCE_SPOT_API, "BTCUSDT", "1m", ts, limit=1500)

    coverage["spot_1m"] = "available" if spot_1m else "unavailable"
    coverage["futures_1m"] = "available" if fut_1m else "unavailable"
    coverage["btc_1m"] = "available" if btc_1m else "unavailable"

    use = spot_1m if spot_1m else fut_1m
    price = _close_at(use, ts)
    features["price"] = price

    for label, sec in RETURN_WINDOWS.items():
        val = _return_over(use, ts, sec) if use else None
        features[f"return_{label}"] = val
        coverage[f"return_{label}"] = "available" if val is not None else "unavailable"

    closes = [float(c[4]) for c in _candles_at_or_before(use, ts)] if use else []
    if len(closes) >= 20:
        ema9 = _ema(closes[-60:], 9)
        ema21 = _ema(closes[-120:], 21)
        features["ema9"] = ema9
        features["ema21"] = ema21
        if ema9 is not None and ema21 is not None and price:
            features["ema9_slope"] = (ema9 - ema21) / price * 100
            features["dist_from_ema9_pct"] = (price - ema9) / price * 100
        coverage["ema"] = "available"
    else:
        coverage["ema"] = "unavailable"

    if use:
        window = _candles_at_or_before(use, ts)[-60:]
        if window:
            highs = [float(c[2]) for c in window]
            lows = [float(c[3]) for c in window]
            if price:
                features["dist_from_local_high_pct"] = (price - max(highs)) / price * 100
                features["dist_from_local_low_pct"] = (price - min(lows)) / price * 100
            coverage["local_extremes"] = "available"
        atr = _atr(_candles_at_or_before(use, ts)[-30:])
        features["atr"] = atr
        if atr and price:
            features["atr_pct"] = atr / price * 100
        coverage["atr"] = "available" if atr else "unavailable"

    funding, funding_cov = _fetch_funding(pair, ts)
    features["funding_rate"] = funding
    coverage["funding_rate"] = funding_cov
    coverage["open_interest"] = "unavailable_historical"
    coverage["oi_delta"] = "unavailable_historical"
    coverage["basis"] = "unavailable_historical"
    coverage["long_short_ratio"] = "unavailable_historical"
    coverage["liquidation_intensity"] = "unavailable_historical"

    btc_ret_1h = _return_over(btc_1m, ts, 3600) if btc_1m else None
    sym_ret_1h = features.get("return_1h")
    features["btc_return_1h"] = btc_ret_1h
    if btc_ret_1h is not None and sym_ret_1h is not None:
        features["relative_strength_1h"] = sym_ret_1h - btc_ret_1h
    coverage["btc_context"] = "available" if btc_ret_1h is not None else "unavailable"

    if sym_ret_1h is not None:
        if sym_ret_1h > 0.3:
            features["market_regime"] = "bull"
        elif sym_ret_1h < -0.3:
            features["market_regime"] = "bear"
        else:
            features["market_regime"] = "sideways"
    else:
        features["market_regime"] = "unknown"

    return features, coverage


def snapshot_signals(conn: sqlite3.Connection, *, limit: int | None = None) -> dict[str, int]:
    q = f"SELECT id, symbol, timestamp FROM {SIGNALS_TABLE} WHERE symbol IS NOT NULL ORDER BY timestamp"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q).fetchall()
    stats = {"processed": 0, "stored": 0, "skipped": 0}

    for row in rows:
        stats["processed"] += 1
        existing = conn.execute(
            f"SELECT 1 FROM {SNAPSHOTS_TABLE} WHERE signal_id = ?", (row["id"],),
        ).fetchone()
        if existing:
            stats["skipped"] += 1
            continue
        features, coverage = build_market_snapshot(row["symbol"], int(row["timestamp"]))
        conn.execute(
            f"""
            INSERT INTO {SNAPSHOTS_TABLE} (signal_id, snapshot_ts, features_json, coverage_json)
            VALUES (?, ?, ?, ?)
            """,
            (row["id"], int(row["timestamp"]), json.dumps(features), json.dumps(coverage)),
        )
        stats["stored"] += 1
    return stats
