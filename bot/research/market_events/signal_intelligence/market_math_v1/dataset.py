"""Load full S42⋈S55 corpus for market mathematics (no artificial small LIMIT)."""

from __future__ import annotations

import json
import logging
import os
import sys
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

# Full history — never default to 50. Only apply LIMIT when env/arg is set.
_ENV_LIMIT = os.environ.get("MARKET_MATH_LIMIT")


CLOSED_S42_SQL = """
SELECT COUNT(*)
FROM market_events_paper_trades_s42 p
WHERE p.status = 'CLOSED' AND p.pnl_pct IS NOT NULL
"""

S55_COUNT_SQL = """
SELECT COUNT(*)
FROM market_events_trade_features_s55
"""

JOIN_SQL = """
SELECT p.*,
       f.gate_decision, f.market_regime, f.ai_score, f.macro_score, f.news_score,
       f.volatility, f.atr, f.rsi, f.funding, f.oi_delta, f.spread, f.volume,
       f.fear_greed, f.trend, f.hour, f.weekday, f.features_json,
       f.mfe_pct AS f_mfe, f.mae_pct AS f_mae, f.duration_sec,
       f.pnl_usd AS f_pnl_usd, f.pnl_pct AS f_pnl_pct
FROM market_events_paper_trades_s42 p
INNER JOIN market_events_trade_features_s55 f ON f.paper_trade_id = p.id
WHERE p.status = 'CLOSED' AND p.pnl_pct IS NOT NULL
ORDER BY COALESCE(p.closed_at, p.updated_at, p.id) ASC
"""

JOIN_COUNT_SQL = """
SELECT COUNT(*)
FROM market_events_paper_trades_s42 p
INNER JOIN market_events_trade_features_s55 f ON f.paper_trade_id = p.id
WHERE p.status = 'CLOSED' AND p.pnl_pct IS NOT NULL
"""


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


def _safe_count(conn: Any, sql: str) -> int:
    try:
        row = conn.execute(sql).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:
        logger.warning("market_math: count failed: %s", exc)
        return 0


def count_corpus(conn: Any) -> dict[str, int]:
    """Row counts for CLOSED S42, S55, and INNER JOIN match set."""
    return {
        "closed_s42": _safe_count(conn, CLOSED_S42_SQL),
        "s55": _safe_count(conn, S55_COUNT_SQL),
        "matched": _safe_count(conn, JOIN_COUNT_SQL),
    }


def print_load_stats(stats: dict[str, int], *, file: Any = None) -> None:
    out = file or sys.stdout
    print("Loaded CLOSED trades:", file=out)
    print(stats.get("closed_s42", 0), file=out)
    print("", file=out)
    print("Loaded S55 rows:", file=out)
    print(stats.get("s55", 0), file=out)
    print("", file=out)
    print("Matched rows:", file=out)
    print(stats.get("matched", 0), file=out)


def load_all_closed_trade_rows(
    conn: Any,
    *,
    limit: int | None = None,
    print_stats: bool = False,
) -> list[dict[str, Any]]:
    """Load ALL CLOSED S42 trades INNER JOINed with S55 (no default LIMIT 50)."""
    stats = count_corpus(conn)
    if print_stats:
        print_load_stats(stats)

    # Resolve optional limit: explicit arg > env > no limit (full history).
    lim: int | None
    if limit is not None:
        lim = int(limit)
    elif _ENV_LIMIT is not None and str(_ENV_LIMIT).strip() != "":
        lim = int(_ENV_LIMIT)
    else:
        lim = None

    sql = JOIN_SQL
    params: tuple[Any, ...] = ()
    if lim is not None:
        sql = JOIN_SQL + "\nLIMIT ?"
        params = (lim,)

    try:
        rows = [_row(r) for r in conn.execute(sql, params).fetchall()]
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
    print_stats: bool = False,
) -> list[dict[str, Any]]:
    """Full closed book with feature vectors for mathematics research."""
    raw = load_all_closed_trade_rows(conn, limit=limit, print_stats=print_stats)
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
    "CLOSED_S42_SQL",
    "GATE_FILTER_FEATURES",
    "JOIN_COUNT_SQL",
    "JOIN_SQL",
    "NUMERIC_FEATURES",
    "S55_COUNT_SQL",
    "count_corpus",
    "load_all_closed_trade_rows",
    "load_market_math_dataset",
    "print_load_stats",
]
