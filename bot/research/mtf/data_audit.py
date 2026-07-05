"""Multi-timeframe data coverage audit."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.mtf.discovery import discover_15m_market, search_gamma_btc_markets
from bot.research.mtf.snapshots import TABLE, ensure_tables


def audit_data_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_tables(conn)

    # 5m data from existing tables
    mc = conn.execute(
        """
        SELECT COUNT(*) AS n,
               COUNT(DISTINCT market_slug) AS markets,
               MIN(checked_at) AS first_check,
               MAX(checked_at) AS last_check
        FROM market_checks
        WHERE market_slug LIKE 'btc-updown-5m-%'
        """
    ).fetchone()

    v4 = conn.execute(
        """
        SELECT COUNT(*) AS n,
               COUNT(DISTINCT market_slug) AS markets,
               MIN(timestamp) AS first_ts,
               MAX(timestamp) AS last_ts
        FROM v4_shadow_observations
        """
    ).fetchone()

    bidi = conn.execute(
        """
        SELECT COUNT(*) AS closed
        FROM bidirectional_shadow_trades
        WHERE status='closed'
        """
    ).fetchone()

    # MTF snapshots
    snap_count = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    snap_15m = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE market_15m_slug IS NOT NULL"
    ).fetchone()[0]
    snap_1h = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE market_1h_slug IS NOT NULL"
    ).fetchone()[0]
    snap_daily = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE market_daily_slug IS NOT NULL"
    ).fetchone()[0]

    snap_range = conn.execute(
        f"SELECT MIN(timestamp), MAX(timestamp) FROM {TABLE}"
    ).fetchone()

    # Live discovery probe (optional, may fail offline)
    gamma_markets: list[dict] = []
    try:
        gamma_markets = search_gamma_btc_markets(limit=20)
    except Exception:
        pass

    m15 = discover_15m_market()

    slug_patterns = {
        "5m": "btc-updown-5m-{unix_window_start}",
        "15m": "btc-updown-15m-{unix_window_start}",
        "1h": "bitcoin-up-or-down-{date}-{hour}-et (Gamma search)",
        "daily": "bitcoin-up-or-down-on-{month}-{day}-{year} (Gamma search)",
    }

    coverage_15m = snap_15m / snap_count if snap_count else 0
    coverage_1h = snap_1h / snap_count if snap_count else 0
    coverage_daily = snap_daily / snap_count if snap_count else 0

    return {
        "market_checks_5m": dict(mc) if mc else {},
        "v4_observations": dict(v4) if v4 else {},
        "bidi_closed_trades": int(bidi["closed"]) if bidi else 0,
        "snapshot_total": snap_count,
        "snapshot_15m": snap_15m,
        "snapshot_1h": snap_1h,
        "snapshot_daily": snap_daily,
        "snapshot_coverage_15m": round(coverage_15m, 3),
        "snapshot_coverage_1h": round(coverage_1h, 3),
        "snapshot_coverage_daily": round(coverage_daily, 3),
        "snapshot_ts_range": (snap_range[0], snap_range[1]) if snap_range else (None, None),
        "slug_patterns": slug_patterns,
        "gamma_search_results": len(gamma_markets),
        "gamma_sample_slugs": [g.get("slug") for g in gamma_markets[:10]],
        "live_15m_probe": m15.slug if m15 else None,
        "sufficient_for_model_c": (
            snap_count >= 100
            and coverage_15m >= 0.30
            and (coverage_1h >= 0.20 or coverage_daily >= 0.20)
        ),
    }
