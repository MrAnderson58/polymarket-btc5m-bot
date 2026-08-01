"""Load full S42+S55 corpus for market mathematics (no artificial small LIMIT)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from bot.research.market_events.signal_intelligence.feature_store import extract_sample
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

logger = logging.getLogger(__name__)

# Explicit list from the research brief.
NUMERIC_FEATURES: tuple[str, ...] = (
    "rsi",
    "atr",
    "atr_pct",
    "ema20_distance",
    "ema50_distance",
    "ema200_distance",
    "vwap_distance",
    "funding",
    "funding_delta",
    "oi",
    "oi_delta",
    "fear_greed",
    "adx",
    "macd",
    "macd_hist",
    "trend",
    "stoch_k",
    "stoch_d",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "direction",
    "symbol",
)

GATE_FILTER_FEATURES: tuple[str, ...] = (
    "atr",
    "atr_pct",
    "funding",
    "fear_greed",
    "trend",
    "adx",
    "rsi",
    "oi_delta",
)

# Full history — never default to 50.
_FULL_LIMIT = int(os.environ.get("MARKET_MATH_LIMIT", "10000000"))


def _row(r: Any) -> dict[str, Any]:
    if hasattr(r, "keys"):
        return {k: r[k] for k in r.keys()}
    return dict(r)


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


def load_all_closed_trade_rows(conn: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load ALL CLOSED S42 trades joined with S55 (no LIMIT 50)."""
    lim = int(limit if limit is not None else _FULL_LIMIT)
    sql = """
        SELECT p.*,
               f.gate_decision, f.market_regime, f.ai_score, f.macro_score, f.news_score,
               f.volatility, f.atr, f.rsi, f.funding, f.oi_delta, f.spread, f.volume,
               f.fear_greed, f.trend, f.hour, f.weekday, f.features_json,
               f.mfe_pct AS f_mfe, f.mae_pct AS f_mae, f.duration_sec,
               f.pnl_usd AS f_pnl_usd, f.pnl_pct AS f_pnl_pct
        FROM market_events_paper_trades_s42 p
        LEFT JOIN market_events_trade_features_s55 f ON f.paper_trade_id = p.id
        WHERE p.status = 'CLOSED' AND p.pnl_pct IS NOT NULL
        ORDER BY COALESCE(p.closed_at, p.updated_at, p.id) ASC
        LIMIT ?
    """
    try:
        rows = [_row(r) for r in conn.execute(sql, (lim,)).fetchall()]
    except Exception as exc:
        logger.warning("market_math: S42/S55 load failed: %s", exc)
        rows = []
    for r in rows:
        if r.get("mfe_pct") is None and r.get("f_mfe") is not None:
            r["mfe_pct"] = r["f_mfe"]
        if r.get("mae_pct") is None and r.get("f_mae") is not None:
            r["mae_pct"] = r["f_mae"]
        if r.get("pnl_usd") is None and r.get("f_pnl_usd") is not None:
            r["pnl_usd"] = r["f_pnl_usd"]
    return rows


def load_market_math_dataset(
    conn: Any,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Full closed book with feature vectors for mathematics research."""
    raw = load_all_closed_trade_rows(conn, limit=limit)
    out: list[dict[str, Any]] = []
    for r in raw:
        sample = extract_sample(r)
        blob = _parse_blob(r.get("features_json"))
        row: dict[str, Any] = dict(sample)
        for k in NUMERIC_FEATURES + CATEGORICAL_FEATURES + ("mfe_pct", "mae_pct"):
            if row.get(k) is None and blob.get(k) is not None:
                row[k] = blob.get(k)
            if row.get(k) is None and r.get(k) is not None:
                row[k] = r.get(k)
        # aliases
        if row.get("atr_pct") is None:
            atr = _safe_float(row.get("atr"))
            entry = _safe_float(r.get("entry") or row.get("entry"))
            if atr is not None and entry and entry > 0:
                row["atr_pct"] = atr / entry * 100.0
            elif _safe_float(row.get("volatility")) is not None:
                row["atr_pct"] = row.get("volatility")
        if row.get("macd_hist") is None:
            row["macd_hist"] = blob.get("macd_hist") or blob.get("macd_histogram") or row.get("macd")
        if row.get("oi") is None:
            row["oi"] = blob.get("oi") or blob.get("open_interest")
        if row.get("funding_delta") is None:
            row["funding_delta"] = blob.get("funding_delta") or blob.get("funding_change")
        if row.get("stoch_k") is None:
            row["stoch_k"] = blob.get("stoch_k") or blob.get("stochastic") or blob.get("stoch")
        pnl = _safe_float(row.get("pnl"))
        if pnl is None:
            pnl = _safe_float(r.get("pnl_usd"))
        if pnl is None:
            pnl = _safe_float(r.get("pnl_pct"))
        row["pnl"] = pnl
        row["pnl_pct"] = _safe_float(r.get("pnl_pct") if r.get("pnl_pct") is not None else row.get("pnl_pct"))
        row["mfe_pct"] = _safe_float(row.get("mfe_pct") if row.get("mfe_pct") is not None else r.get("mfe_pct"))
        row["mae_pct"] = _safe_float(row.get("mae_pct") if row.get("mae_pct") is not None else r.get("mae_pct"))
        row["symbol"] = str(row.get("symbol") or r.get("symbol") or "").upper()
        row["direction"] = str(row.get("direction") or r.get("direction") or "").upper()
        row["closed_at"] = r.get("closed_at") or row.get("created_at")
        # Drop known Feature Recovery stubs so mathematics uses real values only.
        if _safe_float(row.get("atr")) == 50.0:
            row["atr"] = None
        if _safe_float(row.get("funding")) == 54.1:
            row["funding"] = None
        if _safe_float(row.get("atr_pct")) is not None and abs(float(row["atr_pct"])) > 50:
            # absurd ATR% usually from stub ATR / price mix
            if _safe_float(row.get("atr")) is None:
                row["atr_pct"] = None
        if row.get("pnl") is None:
            continue
        out.append(row)
    return out


__all__ = [
    "CATEGORICAL_FEATURES",
    "GATE_FILTER_FEATURES",
    "NUMERIC_FEATURES",
    "load_all_closed_trade_rows",
    "load_market_math_dataset",
]
