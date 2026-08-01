"""Load Research Lake corpus for Market Edge Discovery V3 (research-only)."""

from __future__ import annotations

import datetime as _dt
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

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
    "bollinger_pct",
    "stoch_k",
    "stoch_d",
    "trend",
    "funding",
    "funding_delta",
    "open_interest",
    "oi_delta",
    "fear_greed",
    "volume",
    "hour",
    "confidence",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "symbol",
    "direction",
    "weekday",
    "gate_decision",
    "alpha_cluster",
    "optimizer_state_key",
    "regime",
)

ALL_FEATURES: tuple[str, ...] = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def _dig(row: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if row.get(k) is not None:
            return row[k]
    return None


def normalize_row(r: dict[str, Any]) -> dict[str, Any]:
    """Flatten lake aliases used by mining."""
    out = dict(r)
    # Nested blobs
    features = out.get("features") if isinstance(out.get("features"), dict) else {}
    macro = out.get("macro") if isinstance(out.get("macro"), dict) else {}
    patterns = out.get("patterns") if isinstance(out.get("patterns"), dict) else {}
    alpha = out.get("alpha_labels") if isinstance(out.get("alpha_labels"), dict) else {}
    opt = out.get("optimizer_state") if isinstance(out.get("optimizer_state"), dict) else {}

    for src in (features, macro, patterns):
        for k, v in src.items():
            if out.get(k) is None and v is not None:
                out[k] = v

    if out.get("gate_decision") is None:
        out["gate_decision"] = out.get("gate") or ""
    if out.get("regime") is None:
        out["regime"] = out.get("market_regime") or patterns.get("market_regime") or ""
    if out.get("atr_pct") is None and out.get("atr") is not None and out.get("entry"):
        try:
            entry = float(out["entry"])
            if entry:
                out["atr_pct"] = float(out["atr"]) / entry * 100.0
        except Exception:
            pass
    if out.get("bollinger_pct") is None:
        out["bollinger_pct"] = _dig(out, "bb_pct", "bollinger_pct_b")
    if out.get("stoch_k") is None:
        out["stoch_k"] = _dig(out, "stochastic_k", "stoch")
    if out.get("open_interest") is None:
        out["open_interest"] = _dig(out, "oi", "open_interest")
    if out.get("funding_delta") is None:
        out["funding_delta"] = _dig(out, "funding_change")

    # Alpha / optimizer categorical
    disc = alpha.get("discovery") if isinstance(alpha.get("discovery"), dict) else {}
    out["alpha_cluster"] = str(
        disc.get("cluster") or alpha.get("cluster") or out.get("alpha_cluster") or ""
    )
    opt_keys = [k for k in opt.keys() if k][:1]
    out["optimizer_state_key"] = str(opt_keys[0] if opt_keys else out.get("optimizer_state_key") or "")

    closed = out.get("closed_at") or out.get("opened_at")
    if out.get("hour") is None and closed is not None:
        try:
            out["hour"] = float(_dt.datetime.utcfromtimestamp(int(closed)).hour)
        except Exception:
            pass
    if out.get("weekday") is None and closed is not None:
        try:
            out["weekday"] = str(_dt.datetime.utcfromtimestamp(int(closed)).weekday())
        except Exception:
            out["weekday"] = ""
    else:
        out["weekday"] = str(out.get("weekday") if out.get("weekday") is not None else "")

    out["direction"] = str(out.get("direction") or "").upper()
    out["symbol"] = str(out.get("symbol") or "").upper()
    out["gate_decision"] = str(out.get("gate_decision") or "").upper()
    out["regime"] = str(out.get("regime") or "").upper()
    out["alpha_cluster"] = str(out.get("alpha_cluster") or "")
    out["optimizer_state_key"] = str(out.get("optimizer_state_key") or "")

    pnl = _safe_float(out.get("pnl"))
    if pnl is None:
        pnl = _safe_float(out.get("pnl_pct")) or _safe_float(out.get("pnl_usd"))
    out["pnl"] = float(pnl or 0.0)
    return out


def load_edge_v3_dataset(
    conn: Any,
    *,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Full CLOSED Research Lake load (single query path — no N+1)."""
    stats: dict[str, Any] = {"source": "empty", "n_raw": 0}
    rows: list[dict[str, Any]] = []
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        n_lake = research_lake_row_count(conn)
        stats["n_lake"] = n_lake
        if n_lake > 0:
            rows = load_research_lake_rows(conn, limit=limit)
            stats["source"] = "research_lake_v1"
        else:
            from bot.research.market_events.signal_intelligence.market_math_v1.dataset import (
                load_market_math_dataset,
            )

            rows = load_market_math_dataset(conn, limit=limit, print_stats=False)
            stats["source"] = "market_math_fallback"
    except Exception as exc:
        stats["error"] = str(exc)
        try:
            from bot.research.market_events.signal_intelligence.market_math_v1.dataset import (
                load_market_math_dataset,
            )

            rows = load_market_math_dataset(conn, limit=limit, print_stats=False)
            stats["source"] = "market_math_fallback"
        except Exception as exc2:
            stats["error2"] = str(exc2)
            return [], stats

    stats["n_raw"] = len(rows)
    out = [normalize_row(r) for r in rows]
    return out, stats


def matrix_from_rows(
    rows: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Build pnl vector + numeric / categorical arrays for vectorized mining."""
    n = len(rows)
    pnls = np.asarray([float(r.get("pnl") or 0.0) for r in rows], dtype=np.float64)
    closed = np.asarray(
        [int(r.get("closed_at") or r.get("opened_at") or i) for i, r in enumerate(rows)],
        dtype=np.int64,
    )
    numeric: dict[str, np.ndarray] = {}
    for feat in NUMERIC_FEATURES:
        arr = np.full(n, np.nan, dtype=np.float64)
        for i, r in enumerate(rows):
            v = _safe_float(r.get(feat))
            if v is not None:
                arr[i] = float(v)
        numeric[feat] = arr
    cats: dict[str, np.ndarray] = {}
    for feat in CATEGORICAL_FEATURES:
        cats[feat] = np.asarray([str(r.get(feat) or "") for r in rows], dtype=object)
    return pnls, closed, numeric, cats


__all__ = [
    "ALL_FEATURES",
    "CATEGORICAL_FEATURES",
    "NUMERIC_FEATURES",
    "load_edge_v3_dataset",
    "matrix_from_rows",
    "normalize_row",
]
