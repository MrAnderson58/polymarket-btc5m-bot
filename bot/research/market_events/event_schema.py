"""Phase E.1 SQLite schema — additive, isolated from trades.db and futures_agent."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

MIGRATIONS_TABLE = "market_events_migrations"
SCHEMA_VERSION = 5

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

    if current < 1:
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (1, now, "Phase E.1 initial schema"),
        )
        applied.append("v1")

    if current < 2:
        conn.executescript(E2_DDL)
        for stmt in E2_ALTER_STATEMENTS:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (2, now, "Phase E.2 multi-asset instrument registry"),
        )
        applied.append("v2")
        current = 2

    if current < 3:
        for stmt in E21_ALTER_STATEMENTS:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (3, now, "Phase E.2.1 activation tiers and observation mode"),
        )
        applied.append("v3")
        current = 3

    if current < 4:
        conn.executescript(E3_DDL)
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (4, now, "Phase E.3 lifecycle and shadow research tables"),
        )
        applied.append("v4")
        current = 4

    if current < SCHEMA_VERSION:
        conn.executescript(E31_DDL)
        for stmt in E31_ALTER_STATEMENTS:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (5, now, "Phase E.3.1 pending reversal watcher and shadow research"),
        )
        applied.append("v5")
        conn.commit()
    elif not applied:
        conn.commit()
    return applied


E2_DDL = """
CREATE TABLE IF NOT EXISTS market_events_instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    venue_symbol TEXT NOT NULL,
    canonical_asset TEXT NOT NULL,
    reference_asset TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    quote_currency TEXT NOT NULL DEFAULT 'USDT',
    trading_hours_mode TEXT NOT NULL,
    price_source TEXT NOT NULL,
    reference_price_source TEXT NOT NULL,
    contract_type TEXT,
    funding_applicable INTEGER NOT NULL DEFAULT 0,
    leverage_available INTEGER NOT NULL DEFAULT 1,
    liquidity_tier TEXT NOT NULL DEFAULT 'WATCH',
    active INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(venue, venue_symbol)
);

CREATE INDEX IF NOT EXISTS idx_me_inst_asset ON market_events_instruments(canonical_asset);
CREATE INDEX IF NOT EXISTS idx_me_inst_class ON market_events_instruments(asset_class);
CREATE INDEX IF NOT EXISTS idx_me_inst_active ON market_events_instruments(active);

CREATE TABLE IF NOT EXISTS market_events_price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL,
    obs_ts INTEGER NOT NULL,
    trade_price REAL,
    reference_price REAL,
    basis_bps REAL,
    spread_bps REAL,
    lag_seconds INTEGER,
    venue TEXT,
    raw_json TEXT,
    FOREIGN KEY (instrument_id) REFERENCES market_events_instruments(id)
);

CREATE INDEX IF NOT EXISTS idx_me_price_obs_inst ON market_events_price_observations(instrument_id);

CREATE TABLE IF NOT EXISTS market_events_entity_registry (
    entity_id TEXT PRIMARY KEY,
    canonical_asset TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    metadata_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_discovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    discovered_count INTEGER NOT NULL,
    run_ts INTEGER NOT NULL,
    provenance_json TEXT
);
"""

E2_ALTER_STATEMENTS = [
    "ALTER TABLE market_events ADD COLUMN instrument_id INTEGER",
    "ALTER TABLE market_events ADD COLUMN asset_class TEXT",
    "ALTER TABLE market_events ADD COLUMN session_regime TEXT",
    "ALTER TABLE market_events ADD COLUMN reference_return_pct REAL",
    "ALTER TABLE market_events ADD COLUMN basis_bps REAL",
    "ALTER TABLE market_events ADD COLUMN cross_classification TEXT",
    "ALTER TABLE market_event_snapshots ADD COLUMN reference_price REAL",
    "ALTER TABLE market_event_snapshots ADD COLUMN basis_bps REAL",
    "ALTER TABLE market_event_snapshots ADD COLUMN tracking_error_bps REAL",
]

E21_ALTER_STATEMENTS = [
    "ALTER TABLE market_events_instruments ADD COLUMN activation_tier TEXT DEFAULT 'INACTIVE'",
    "ALTER TABLE market_events_instruments ADD COLUMN observe_enabled INTEGER DEFAULT 0",
    "ALTER TABLE market_events_instruments ADD COLUMN paper_enabled INTEGER DEFAULT 0",
    "ALTER TABLE market_events_instruments ADD COLUMN reference_provider TEXT",
    "ALTER TABLE market_events_price_observations ADD COLUMN session_regime TEXT",
    "ALTER TABLE market_events_price_observations ADD COLUMN turnover_24h REAL",
    "ALTER TABLE market_events_price_observations ADD COLUMN volume_24h REAL",
    "ALTER TABLE market_events_price_observations ADD COLUMN quote_age_sec REAL",
    "ALTER TABLE market_events_price_observations ADD COLUMN reference_provider TEXT",
    "ALTER TABLE market_events_price_observations ADD COLUMN same_venue_reference INTEGER DEFAULT 1",
]

E3_DDL = """
CREATE TABLE IF NOT EXISTS market_event_lifecycle_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    decision_ts INTEGER NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    details_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_lifecycle_event ON market_event_lifecycle_decisions(event_id);
CREATE INDEX IF NOT EXISTS idx_me_lifecycle_stage ON market_event_lifecycle_decisions(stage);

CREATE TABLE IF NOT EXISTS market_events_shadow_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    detector_id TEXT NOT NULL DEFAULT 'SHOCK_F',
    direction TEXT NOT NULL,
    return_pct REAL,
    z_score REAL,
    vol_scale_pct REAL,
    overlap_detectors_json TEXT,
    forward_returns_json TEXT,
    path_class TEXT,
    detector_version TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_shadow_sym ON market_events_shadow_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_me_shadow_ts ON market_events_shadow_candidates(event_ts);
"""

E31_DDL = """
CREATE TABLE IF NOT EXISTS market_events_pending_shocks (
    event_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'MONITORING_REVERSAL',
    detected_ts INTEGER NOT NULL,
    shock_return_pct REAL,
    shock_extreme_price REAL,
    shock_extreme_ts INTEGER,
    monitor_until_ts INTEGER NOT NULL,
    monitor_horizons_json TEXT,
    confirmed_reversal TEXT,
    confirm_ts INTEGER,
    confirm_latency_sec INTEGER,
    confirm_price REAL,
    path_from_extreme_json TEXT,
    r5_first_confirm_ts INTEGER,
    expired_ts INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_pending_phase ON market_events_pending_shocks(phase);
CREATE INDEX IF NOT EXISTS idx_me_pending_sym ON market_events_pending_shocks(symbol);

CREATE TABLE IF NOT EXISTS market_events_profile_shadow_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    window_sec INTEGER NOT NULL,
    direction TEXT NOT NULL,
    return_pct REAL,
    threshold_pct REAL,
    min_abs_floor_pct REAL,
    session_regime TEXT,
    asset_class TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_prof_shadow_sym ON market_events_profile_shadow_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_me_prof_shadow_prof ON market_events_profile_shadow_candidates(profile_name);

CREATE TABLE IF NOT EXISTS market_events_counterfactual_studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    episode_ts INTEGER NOT NULL,
    direction TEXT NOT NULL,
    shock_return_pct REAL,
    max_continuation_pct REAL,
    time_to_extreme_sec INTEGER,
    max_reversal_pct REAL,
    time_to_25_reclaim_sec INTEGER,
    time_to_50_reclaim_sec INTEGER,
    time_to_full_reclaim_sec INTEGER,
    forward_returns_json TEXT,
    mae_mfe_json TEXT,
    session_regime TEXT,
    has_context INTEGER DEFAULT 0,
    classification TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_cf_sym ON market_events_counterfactual_studies(symbol);
CREATE INDEX IF NOT EXISTS idx_me_cf_ts ON market_events_counterfactual_studies(episode_ts);
"""

E31_ALTER_STATEMENTS = [
    "ALTER TABLE paper_strategy_runs ADD COLUMN stop_history_json TEXT",
    "ALTER TABLE paper_strategy_runs ADD COLUMN be_helped INTEGER",
    "ALTER TABLE market_events_shadow_candidates ADD COLUMN profile_name TEXT",
    "ALTER TABLE market_events_shadow_candidates ADD COLUMN raw_tick_count INTEGER",
    "ALTER TABLE market_events_shadow_candidates ADD COLUMN episode_id TEXT",
    "ALTER TABLE market_events_shadow_candidates ADD COLUMN deduped INTEGER DEFAULT 0",
]
