"""Research Lake V1 schema + version constants."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.feature_store import FEATURE_VERSION as FS_FEATURE_VERSION

SCHEMA_VERSION_LAKE = "1.0.0"
DATASET_VERSION = "rlake-v1"
FEATURE_VERSION = str(FS_FEATURE_VERSION)

LAKE_TABLE = "market_events_research_lake_v1"
BUILD_TABLE = "market_events_research_lake_builds_v1"
META_TABLE = "market_events_research_lake_meta_v1"

RESEARCH_LAKE_DDL = f"""
CREATE TABLE IF NOT EXISTS {LAKE_TABLE} (
    trade_id INTEGER PRIMARY KEY,
    symbol TEXT,
    direction TEXT,
    entry REAL,
    exit REAL,
    result TEXT,
    pnl REAL,
    pnl_pct REAL,
    gate TEXT,
    confidence REAL,
    regime TEXT,
    features_json TEXT,
    macro_json TEXT,
    news_json TEXT,
    patterns_json TEXT,
    alpha_labels_json TEXT,
    optimizer_state_json TEXT,
    experiment_state_json TEXT,
    feature_version TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    s55_id INTEGER,
    s56_id INTEGER,
    g31_candidate_id INTEGER,
    status TEXT,
    closed_at INTEGER,
    opened_at INTEGER,
    updated_at INTEGER NOT NULL,
    built_at INTEGER NOT NULL,
    row_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_rlake_v1_closed
    ON {LAKE_TABLE}(closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_rlake_v1_symbol
    ON {LAKE_TABLE}(symbol, direction, closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_rlake_v1_status
    ON {LAKE_TABLE}(status, closed_at DESC);

CREATE TABLE IF NOT EXISTS {BUILD_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_ts INTEGER NOT NULL,
    mode TEXT NOT NULL,
    rows_seen INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_updated INTEGER NOT NULL DEFAULT 0,
    rows_skipped INTEGER NOT NULL DEFAULT 0,
    health_json TEXT,
    dataset_version TEXT,
    feature_version TEXT,
    schema_version TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS {META_TABLE} (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER NOT NULL
);
"""


def ensure_research_lake_schema(conn: Any) -> None:
    """Idempotent create of Research Lake V1 tables."""
    try:
        conn.executescript(RESEARCH_LAKE_DDL)
    except Exception:
        pass


__all__ = [
    "BUILD_TABLE",
    "DATASET_VERSION",
    "FEATURE_VERSION",
    "LAKE_TABLE",
    "META_TABLE",
    "RESEARCH_LAKE_DDL",
    "SCHEMA_VERSION_LAKE",
    "ensure_research_lake_schema",
]
