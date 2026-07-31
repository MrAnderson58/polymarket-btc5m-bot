"""Enrich S55 entry features from live candles + snapshots (no stubs)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.feature_recovery_v2.indicators import (
    indicators_from_bars,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

logger = logging.getLogger(__name__)

# Known historical placeholder values to reject when real calc is available.
_FAKE_ATR = {50.0, 50}
_FAKE_FUNDING = {54.1, 54.10}
_FAKE_VOL = {50.0, 0.0}


def _is_fake_atr(v: float | None) -> bool:
    if v is None:
        return True
    return float(v) in _FAKE_ATR or float(v) <= 0


def _is_fake_funding(v: float | None) -> bool:
    if v is None:
        return False
    # Real funding is typically |f| << 1; 54.1 was a score-scale stub
    return float(v) in _FAKE_FUNDING or abs(float(v)) > 5.0


def load_snapshot_series(conn: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT snapshot_ts, funding, open_interest, atr, fear_greed, volume,
                   btc_dominance, btc_price
            FROM market_snapshots_g3
            ORDER BY snapshot_ts DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception:
        try:
            rows = conn.execute(
                """
                SELECT snapshot_ts, funding, open_interest, atr, fear_greed, volume
                FROM market_snapshots_g3
                ORDER BY snapshot_ts DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except Exception:
            return []
    out = []
    for r in rows:
        if hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append({
                "snapshot_ts": r[0], "funding": r[1], "open_interest": r[2],
                "atr": r[3], "fear_greed": r[4], "volume": r[5],
            })
    return out


def enrich_entry_features(
    conn: Any,
    feats: dict[str, Any],
    *,
    entry_price: float | None = None,
    candle_limit: int = 220,
) -> dict[str, Any]:
    """Mutate/return feats with real candle indicators; strip stub aliases."""
    symbol = str(feats.get("symbol") or feats.get("coin") or "").upper()
    entry = entry_price
    if entry is None:
        entry = _safe_float(feats.get("entry") or feats.get("close"))

    bars = []
    if symbol:
        try:
            bars = load_recent_candles(conn, symbol=symbol, limit=candle_limit)
        except Exception as exc:
            logger.warning("candle load failed for %s: %s", symbol, exc)

    candle_feats: dict[str, Any] = {}
    if len(bars) >= 20:
        candle_feats = indicators_from_bars(bars, entry=entry)
        feats["_candle_source"] = "market_events_historical_candles"
        feats["_candle_n"] = len(bars)
        feats["_candle_last_ts"] = bars[-1].open_ts
    else:
        feats["_candle_source"] = "BROKEN_SOURCE" if not bars else "INSUFFICIENT_BARS"
        feats["_candle_n"] = len(bars)

    # Apply candle features (overwrite stubs)
    for k, v in candle_feats.items():
        if k in ("close", "candle_n"):
            continue
        if v is not None:
            feats[k] = v

    # ATR: prefer candle ATR; reject fake 50
    if _is_fake_atr(_safe_float(feats.get("atr"))):
        if candle_feats.get("atr") is not None:
            feats["atr"] = candle_feats["atr"]
        else:
            feats["atr"] = None
    # volatility is realized vol proxy from ATR% — NOT a duplicate of ATR absolute
    atr_pct = candle_feats.get("atr_pct")
    if atr_pct is not None:
        feats["volatility"] = atr_pct
    elif _safe_float(feats.get("volatility")) in _FAKE_VOL or feats.get("volatility") == feats.get("atr"):
        feats["volatility"] = None

    # Volume from candles MA when snapshot volume is missing/zero stub
    if candle_feats.get("volume_ma20") is not None:
        if _safe_float(feats.get("volume")) in (None, 0.0):
            feats["volume"] = candle_feats["volume_ma20"]

    # Snapshot funding / OI / fear — with deltas (not stubs)
    snaps = load_snapshot_series(conn, limit=8)
    if snaps:
        cur = snaps[0]
        funding = _safe_float(cur.get("funding"))
        if funding is not None and not _is_fake_funding(funding):
            feats["funding"] = funding
        elif _is_fake_funding(_safe_float(feats.get("funding"))):
            feats["funding"] = None

        oi = _safe_float(cur.get("open_interest"))
        if oi is not None:
            feats["open_interest"] = oi
        fear = _safe_float(cur.get("fear_greed"))
        if fear is not None:
            feats["fear_greed"] = fear
        if feats.get("btc_dominance") is None:
            feats["btc_dominance"] = _safe_float(cur.get("btc_dominance"))

        # funding_delta / oi_delta from consecutive snapshots
        if len(snaps) >= 2:
            prev = snaps[1]
            pf = _safe_float(prev.get("funding"))
            cf = _safe_float(feats.get("funding"))
            if cf is not None and pf is not None and not _is_fake_funding(cf) and not _is_fake_funding(pf):
                feats["funding_delta"] = round(cf - pf, 10)
            poi = _safe_float(prev.get("open_interest"))
            coi = _safe_float(feats.get("open_interest"))
            if coi is not None and poi is not None:
                feats["oi_delta"] = round(coi - poi, 6)
            # never fall back oi_delta = oi (that was a stub)
            elif feats.get("oi_delta") == feats.get("open_interest"):
                feats["oi_delta"] = None
        feats["_snapshot_ts"] = cur.get("snapshot_ts")

    # funding_sign from real funding
    f = _safe_float(feats.get("funding"))
    if f is not None and not _is_fake_funding(f):
        feats["funding_sign"] = 1 if f > 0 else (-1 if f < 0 else 0)
    else:
        if _is_fake_funding(_safe_float(feats.get("funding"))):
            feats["funding"] = None
        feats["funding_sign"] = None

    # AI score must NOT equal confidence stub — leave independent if present,
    # otherwise None (do not alias).
    conf = _safe_float(feats.get("decision_confidence") or feats.get("confidence"))
    feats["confidence"] = conf
    feats["decision_confidence"] = conf
    ai = _safe_float(feats.get("ai_score"))
    if ai is not None and conf is not None and abs(ai - conf) < 1e-12:
        # was aliased — clear until a real AI producer exists
        feats["ai_score"] = None
        feats["_ai_score_note"] = "cleared_duplicate_of_confidence"
    elif ai is None:
        feats["ai_score"] = None

    # Trend: prefer candle-computed; do not keep constant 0 stub when candles exist
    if candle_feats.get("trend") is not None:
        feats["trend"] = candle_feats["trend"]
        feats["ema_trend"] = candle_feats.get("ema_trend", candle_feats["trend"])
    elif _safe_float(feats.get("trend")) == 0.0 and feats.get("_candle_n", 0) >= 20:
        feats["trend"] = None

    feats["_feature_recovery_version"] = "v2"
    feats["_enriched_at"] = int(time.time())

    # Refresh features_json
    try:
        payload = {k: v for k, v in feats.items() if k != "features_json"}
        feats["features_json"] = json.dumps(payload, default=str)
    except Exception:
        pass
    return feats


def compute_live_feature_vector(
    conn: Any,
    *,
    symbol: str,
    entry: float | None = None,
) -> dict[str, Any]:
    """Standalone live vector for health/proof (read-only)."""
    feats: dict[str, Any] = {"symbol": str(symbol).upper()}
    return enrich_entry_features(conn, feats, entry_price=entry)


__all__ = [
    "compute_live_feature_vector",
    "enrich_entry_features",
    "load_snapshot_series",
]
