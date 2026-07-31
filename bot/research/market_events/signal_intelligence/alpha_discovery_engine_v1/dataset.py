"""Load S42 corpus and attach recovered feature vectors for alpha mining."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.feature_recovery_v2.indicators import (
    indicators_from_bars,
)
from bot.research.market_events.signal_intelligence.feature_store import (
    extract_sample,
    load_closed_trade_rows,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

logger = logging.getLogger(__name__)

NUMERIC_FEATURES: tuple[str, ...] = (
    "rsi",
    "ema20_distance",
    "ema50_distance",
    "ema200_distance",
    "vwap_distance",
    "atr",
    "atr_pct",
    "adx",
    "macd",
    "macd_hist",
    "stoch_k",
    "stoch_d",
    "bb_pct_b",
    "slope",
    "trend",
    "funding",
    "funding_delta",
    "oi_delta",
    "fear_greed",
    "volume",
    "volatility",
    "confidence",
    "hour",
    "weekday",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "symbol",
    "direction",
    "gate_decision",
    "market_regime",
)


def _parse_blob(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _needs_candle_backfill(row: dict[str, Any]) -> bool:
    for k in ("rsi", "ema20_distance", "vwap_distance", "adx", "macd", "stoch_k"):
        if _safe_float(row.get(k)) is None:
            return True
    atr = _safe_float(row.get("atr"))
    if atr is None or atr == 50.0:
        return True
    return False


def _backfill_from_candles(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    sym = str(row.get("symbol") or "").upper().replace("USDT", "")
    if not sym:
        return row
    try:
        bars = load_recent_candles(conn, symbol=sym, limit=220)
    except Exception as exc:
        logger.debug("alpha candle backfill failed %s: %s", sym, exc)
        return row
    if len(bars) < 30:
        return row
    entry = _safe_float(row.get("entry"))
    inds = indicators_from_bars(bars, entry=entry)
    for k, v in inds.items():
        if k in ("close", "candle_n"):
            continue
        if v is None:
            continue
        cur = row.get(k)
        if cur is None or (k == "atr" and _safe_float(cur) == 50.0):
            row[k] = v
        elif k.endswith("_distance") and _safe_float(cur) is None:
            row[k] = v
    if row.get("volatility") is None and inds.get("atr_pct") is not None:
        row["volatility"] = inds["atr_pct"]
    if row.get("trend") in (None, 0, 0.0) and inds.get("trend") is not None:
        row["trend"] = inds["trend"]
    row["_candle_backfill"] = True
    return row


def load_alpha_dataset(
    conn: Any,
    *,
    limit: int | None = None,
    backfill_candles: bool = True,
) -> list[dict[str, Any]]:
    """Full S42 closed book with recovered features (candle backfill when stubs)."""
    limit = int(limit or os.environ.get("ALPHA_ENGINE_LIMIT", "100000"))
    raw_rows = load_closed_trade_rows(conn, limit=limit)
    out: list[dict[str, Any]] = []
    backfill_budget = int(os.environ.get("ALPHA_ENGINE_BACKFILL_MAX", "5000"))
    backfilled = 0
    for r in raw_rows:
        sample = extract_sample(r)
        blob = _parse_blob(r.get("features_json"))
        # Merge: prefer sample distances; fill from blob / columns
        row: dict[str, Any] = dict(sample)
        for k in NUMERIC_FEATURES + CATEGORICAL_FEATURES:
            if row.get(k) is None and blob.get(k) is not None:
                row[k] = blob.get(k)
            if row.get(k) is None and r.get(k) is not None:
                row[k] = r.get(k)
        # labels
        pnl = _safe_float(row.get("pnl"))
        if pnl is None:
            pnl = _safe_float(r.get("pnl_usd"))
        if pnl is None:
            pnl = _safe_float(r.get("pnl_pct"))
        row["pnl"] = pnl
        row["symbol"] = str(row.get("symbol") or r.get("symbol") or "").upper()
        row["direction"] = str(row.get("direction") or r.get("direction") or "").upper()
        row["closed_at"] = r.get("closed_at") or row.get("created_at")
        row["entry"] = _safe_float(r.get("entry"))
        if backfill_candles and backfilled < backfill_budget and _needs_candle_backfill(row):
            row = _backfill_from_candles(conn, row)
            backfilled += 1
        if row.get("pnl") is None:
            continue
        out.append(row)
    return out


__all__ = [
    "CATEGORICAL_FEATURES",
    "NUMERIC_FEATURES",
    "load_alpha_dataset",
]
