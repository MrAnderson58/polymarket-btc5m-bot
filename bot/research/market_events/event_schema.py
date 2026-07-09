"""Phase E.1 SQLite schema — additive, isolated from trades.db and futures_agent."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

MIGRATIONS_TABLE = "market_events_migrations"
SCHEMA_VERSION = 1

E1_DDL = """
CREATE TABLE IF NOT EXISTS market_events_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_universe_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_tag TEXT NOT NULL,
    symbols_json TEXT NOT NULL,
    selection_reason TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts INTEGER NOT NULL,
    detected_ts INTEGER NOT NULL,
    venue TEXT NOT NULL DEFAULT 'binance_futures',
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'SHOCK',
    direction TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'SHOCK_DETECTED',
    trigger_window_seconds INTEGER,
    return_pct REAL,
    velocity REAL,
    acceleration REAL,
    volume_zscore REAL,
    market_return_pct REAL,
    btc_return_pct REAL,
    relative_return_pct REAL,
    classification TEXT NOT NULL DEFAULT 'UNKNOWN',
    confidence REAL,
    detector_version TEXT NOT NULL,
    detector_triggers_json TEXT NOT NULL DEFAULT '[]',
    dedup_key TEXT,
    raw_metrics_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_me_events_ts ON market_events(event_ts);
CREATE INDEX IF NOT EXISTS idx_me_events_symbol ON market_events(symbol);
CREATE INDEX IF NOT EXISTS idx_me_events_phase ON market_events(phase);

CREATE TABLE IF NOT EXISTS market_event_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    snapshot_ts INTEGER NOT NULL,
    offset_seconds INTEGER NOT NULL,
    price REAL,
    return_from_event REAL,
    volume REAL,
    bid REAL,
    ask REAL,
    spread_bps REAL,
    orderbook_imbalance REAL,
    open_interest REAL,
    funding REAL,
    context_json TEXT,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_snapshots_event ON market_event_snapshots(event_id);

CREATE TABLE IF NOT EXISTS paper_strategy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    reversal_variant TEXT NOT NULL,
    exit_variant TEXT NOT NULL,
    eligibility INTEGER NOT NULL DEFAULT 0,
    rejection_reason TEXT,
    signal_ts INTEGER,
    entry_ts INTEGER,
    entry_price REAL,
    initial_stop REAL,
    breakeven_trigger REAL,
    trailing_mode TEXT,
    take_profit_mode TEXT,
    exit_ts INTEGER,
    exit_price REAL,
    exit_reason TEXT,
    gross_return REAL,
    fee_bps REAL,
    slippage_bps REAL,
    net_return REAL,
    mfe REAL,
    mae REAL,
    be_exit INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER,
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, strategy_name, reversal_variant, exit_variant),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_runs_event ON paper_strategy_runs(event_id);
CREATE INDEX IF NOT EXISTS idx_me_runs_strategy ON paper_strategy_runs(strategy_name);

CREATE TABLE IF NOT EXISTS market_event_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    context_type TEXT NOT NULL,
    source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    context_ts INTEGER NOT NULL,
    time_delta_seconds INTEGER NOT NULL,
    relevance_score REAL,
    context_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, context_type, source, source_record_id),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_context_event ON market_event_context(event_id);
CREATE INDEX IF NOT EXISTS idx_me_context_type ON market_event_context(context_type);

CREATE TABLE IF NOT EXISTS market_events_runner_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_poll_ts INTEGER,
    symbols_json TEXT,
    updated_at INTEGER NOT NULL
);
"""


def apply_migrations(conn: Any) -> list[str]:
    applied: list[str] = []
    conn.executescript(E1_DDL)
    row = conn.execute(
        f"SELECT MAX(version) AS v FROM {MIGRATIONS_TABLE}",
    ).fetchone()
    current = int(row["v"] or 0)
    if current < SCHEMA_VERSION:
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (SCHEMA_VERSION, now, "Phase E.1 initial schema"),
        )
        applied.append(f"v{SCHEMA_VERSION}")
        conn.commit()
    return applied
