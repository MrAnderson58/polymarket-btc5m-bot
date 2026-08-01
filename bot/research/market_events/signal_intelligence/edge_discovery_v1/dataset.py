"""Load full S42⋈S55 corpus for Edge Discovery (research-only)."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.market_math_v1.dataset import (
    count_corpus,
    load_market_math_dataset,
)

# Features from the Edge Discovery brief.
NUMERIC_FEATURES: tuple[str, ...] = (
    "rsi",
    "ema20_distance",
    "atr_pct",
    "funding",
    "oi_delta",
    "fear_greed",
    "trend",
    "hour",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "direction",
    "gate_decision",
    "symbol",
    "weekday",  # time facet
)

ALL_FEATURES: tuple[str, ...] = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def load_edge_dataset(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Full-history CLOSED S42 INNER JOIN S55 + load stats."""
    stats = count_corpus(conn)
    rows = load_market_math_dataset(conn, limit=limit, print_stats=True)
    # Normalize gate / time aliases for mining.
    for r in rows:
        if r.get("gate_decision") is None:
            r["gate_decision"] = r.get("gate") or r.get("decision") or ""
        if r.get("hour") is None and r.get("closed_at") is not None:
            try:
                import datetime as _dt

                r["hour"] = float(_dt.datetime.utcfromtimestamp(int(r["closed_at"])).hour)
            except Exception:
                pass
        if r.get("weekday") is None and r.get("closed_at") is not None:
            try:
                import datetime as _dt

                r["weekday"] = str(_dt.datetime.utcfromtimestamp(int(r["closed_at"])).weekday())
            except Exception:
                pass
        else:
            r["weekday"] = str(r.get("weekday") if r.get("weekday") is not None else "")
        r["direction"] = str(r.get("direction") or "").upper()
        r["symbol"] = str(r.get("symbol") or "").upper()
        r["gate_decision"] = str(r.get("gate_decision") or "").upper()
    return rows, stats


__all__ = [
    "ALL_FEATURES",
    "CATEGORICAL_FEATURES",
    "NUMERIC_FEATURES",
    "load_edge_dataset",
]
