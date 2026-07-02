"""Shared batch context for Trading Intelligence (no SQL in loops)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from bot.optimizer.dataset import load_trade_features
from bot.perf.market_cache import MarketDataCache


@dataclass
class IntelligenceContext:
    cache: MarketDataCache
    slippage_by_slug: dict[str, float] = field(default_factory=dict)
    feature_by_trade_id: dict[int, dict[str, Any]] = field(default_factory=dict)


def load_slippage_map(conn: sqlite3.Connection, market_slugs: set[str]) -> dict[str, float]:
    if not market_slugs:
        return {}
    rows = conn.execute(
        """
        SELECT idempotency_key, slippage
        FROM order_fill_audit
        WHERE fill_status IN ('filled', 'partial')
          AND slippage IS NOT NULL
        ORDER BY id DESC
        """
    ).fetchall()
    out: dict[str, float] = {}
    for slug in market_slugs:
        for row in rows:
            key = row["idempotency_key"] or ""
            if slug in key:
                out[slug] = float(row["slippage"])
                break
    return out


def build_intelligence_context(
    conn: sqlite3.Connection,
    closed: list[Any],
) -> IntelligenceContext:
    cache = MarketDataCache.build(conn, closed)
    slugs = {str(t["market_slug"]) for t in closed}
    features = load_trade_features(conn)
    return IntelligenceContext(
        cache=cache,
        slippage_by_slug=load_slippage_map(conn, slugs),
        feature_by_trade_id={int(r["trade_id"]): dict(r) for r in features},
    )
