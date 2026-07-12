"""Phase E.1 SQLite schema — additive, isolated from trades.db and futures_agent."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

MIGRATIONS_TABLE = "market_events_migrations"
SCHEMA_VERSION = 21

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
    from bot.research.market_events.db import connection_is_postgres

    if connection_is_postgres(conn):
        from bot.research.market_events.schema_pg import apply_pg_migrations
        return apply_pg_migrations(conn)

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

    if current < 5:
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
        current = 5

    if current < 6:
        conn.executescript(E32_DDL)
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (6, now, "Phase E.3.2 near-miss summaries and collector observability"),
        )
        applied.append("v6")
        current = 6

    if current < 7:
        conn.executescript(E33_DDL)
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (7, now, "Phase E.3.3 Telegram alerts and AI analyst shadow"),
        )
        applied.append("v7")
        current = 7

    if current < 8:
        conn.executescript(E4_DDL)
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (8, now, "Phase E.4 historical replay and context intelligence"),
        )
        applied.append("v8")
        current = 8

    if current < SCHEMA_VERSION:
        if current < 9:
            conn.executescript(E5_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (9, now, "Phase E.5 alert engine dashboard and research ops"),
            )
            applied.append("v9")
            current = 9

        if current < 10:
            conn.executescript(E53_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (10, now, "Phase E.5.3 Telegram delivery log and ops"),
            )
            applied.append("v10")
            current = 10

        if current < 11:
            for stmt in E531_ALTER_STATEMENTS:
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
                (11, now, "Phase E.5.3.1 delivery log message_text for retries"),
            )
            applied.append("v11")
            current = 11

        if current < 12:
            conn.executescript(F0_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (12, now, "Phase F.0 signal intelligence research"),
            )
            applied.append("v12")
            current = 12

        if current < 13:
            conn.executescript(F1_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (13, now, "Phase F.1 Telegram signal intelligence reports"),
            )
            applied.append("v13")
            current = 13

        if current < 14:
            conn.executescript(F2_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (14, now, "Phase F.2 professional trading intelligence"),
            )
            applied.append("v14")
            current = 14

        if current < 15:
            conn.executescript(F3_TREND_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (15, now, "Phase F.3 trend shock intelligence"),
            )
            applied.append("v15")
            current = 15

        if current < 16:
            conn.executescript(F4_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (16, now, "Phase F.4 visual intelligence and trend shock v2"),
            )
            applied.append("v16")
            current = 16

        if current < 17:
            for stmt in F41_ALTER_STATEMENTS:
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
                (17, now, "Phase F.4.1 Telegram dedupe and final alert stages"),
            )
            applied.append("v17")
            current = 17

        if current < 18:
            conn.executescript(F5_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (18, now, "Phase F.5 professional signal engine"),
            )
            applied.append("v18")
            current = 18

        if current < 19:
            conn.executescript(F51_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (19, now, "Phase F.5.1 signal pipeline trace"),
            )
            applied.append("v19")
            current = 19

        if current < 20:
            conn.executescript(F6_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (20, now, "Phase F.6 trader performance learning"),
            )
            applied.append("v20")
            current = 20

        if current < 21:
            conn.executescript(F7_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (21, now, "Phase F.7 market intelligence engine"),
            )
            applied.append("v21")
            current = 21

    if not applied:
        conn.commit()
    else:
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

E32_DDL = """
CREATE TABLE IF NOT EXISTS market_events_near_miss_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    window_sec INTEGER NOT NULL,
    direction TEXT NOT NULL,
    session_regime TEXT,
    max_abs_return_pct REAL,
    current_abs_return_pct REAL,
    p95_abs_return_pct REAL,
    p99_abs_return_pct REAL,
    threshold_pct REAL NOT NULL,
    max_threshold_reached_pct REAL,
    max_volume_zscore REAL,
    max_relative_return_pct REAL,
    episodes_25pct INTEGER DEFAULT 0,
    episodes_50pct INTEGER DEFAULT 0,
    episodes_75pct INTEGER DEFAULT 0,
    episodes_90pct INTEGER DEFAULT 0,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_near_miss_sym ON market_events_near_miss_summaries(symbol);
CREATE INDEX IF NOT EXISTS idx_me_near_miss_prof ON market_events_near_miss_summaries(profile_name);
CREATE INDEX IF NOT EXISTS idx_me_near_miss_ts ON market_events_near_miss_summaries(created_at);
"""

E33_DDL = """
CREATE TABLE IF NOT EXISTS market_event_alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    message_text TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL,
    error TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_alert_event ON market_event_alert_log(event_id);
CREATE INDEX IF NOT EXISTS idx_me_alert_type ON market_event_alert_log(alert_type);

CREATE TABLE IF NOT EXISTS market_event_analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'e33_shadow_v1',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    updated_at INTEGER,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_ai_job_event ON market_event_analysis_jobs(event_id);
CREATE INDEX IF NOT EXISTS idx_me_ai_job_status ON market_event_analysis_jobs(status);

CREATE TABLE IF NOT EXISTS market_event_ai_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    job_id INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    structured_output_json TEXT NOT NULL,
    movement_interpretation TEXT,
    reversal_bias TEXT,
    confidence REAL,
    context_ids_json TEXT,
    latency_ms REAL,
    token_usage_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id),
    FOREIGN KEY (job_id) REFERENCES market_event_analysis_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_me_ai_analysis_event ON market_event_ai_analyses(event_id);
"""

E4_DDL = """
CREATE TABLE IF NOT EXISTS market_events_historical_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    source TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    UNIQUE(venue, symbol, timeframe, open_ts)
);

CREATE INDEX IF NOT EXISTS idx_me_hcandle_sym ON market_events_historical_candles(symbol, timeframe, open_ts);

CREATE TABLE IF NOT EXISTS market_events_candle_backfill_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    source TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    last_cursor_ts INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    gaps_found INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(venue, symbol, timeframe, start_ts, end_ts, source)
);

CREATE TABLE IF NOT EXISTS market_events_replay_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_tag TEXT NOT NULL UNIQUE,
    data_source TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    split_config_json TEXT,
    start_ts INTEGER,
    end_ts INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_replay_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_tag TEXT NOT NULL UNIQUE,
    train_end_ts INTEGER NOT NULL,
    validation_end_ts INTEGER NOT NULL,
    holdout_end_ts INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_replay_shocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT,
    event_ts INTEGER NOT NULL,
    direction TEXT NOT NULL,
    detector_id TEXT NOT NULL,
    impulse_pct REAL NOT NULL,
    impulse_duration_sec INTEGER,
    volume_zscore REAL,
    btc_context_pct REAL,
    market_context_pct REAL,
    session_regime TEXT,
    pre_volatility REAL,
    source_provenance TEXT NOT NULL,
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES market_events_replay_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_me_replay_shock_run ON market_events_replay_shocks(run_id);
CREATE INDEX IF NOT EXISTS idx_me_replay_shock_sym ON market_events_replay_shocks(symbol, event_ts);

CREATE TABLE IF NOT EXISTS market_events_replay_path_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shock_id INTEGER NOT NULL,
    horizon_sec INTEGER NOT NULL,
    continuation_pct REAL,
    reversal_pct REAL,
    reclaim_10_sec INTEGER,
    reclaim_25_sec INTEGER,
    reclaim_50_sec INTEGER,
    reclaim_75_sec INTEGER,
    reclaim_100_sec INTEGER,
    mfe_fade_pct REAL,
    mae_fade_pct REAL,
    path_class TEXT,
    raw_json TEXT,
    FOREIGN KEY (shock_id) REFERENCES market_events_replay_shocks(id),
    UNIQUE(shock_id, horizon_sec)
);

CREATE TABLE IF NOT EXISTS market_events_replay_strategy_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    shock_id INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    split_bucket TEXT,
    entered INTEGER NOT NULL DEFAULT 0,
    entry_ts INTEGER,
    exit_ts INTEGER,
    gross_return_pct REAL,
    net_return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    exit_reason TEXT,
    fee_bps REAL,
    slippage_bps REAL,
    raw_json TEXT,
    FOREIGN KEY (run_id) REFERENCES market_events_replay_runs(id),
    FOREIGN KEY (shock_id) REFERENCES market_events_replay_shocks(id),
    UNIQUE(run_id, shock_id, strategy_id, fee_bps, slippage_bps)
);

CREATE TABLE IF NOT EXISTS market_events_replay_context_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shock_id INTEGER NOT NULL,
    context_source TEXT NOT NULL,
    context_record_id TEXT NOT NULL,
    context_ts INTEGER NOT NULL,
    age_at_event_sec INTEGER NOT NULL,
    window_sec INTEGER NOT NULL,
    symbol_match INTEGER NOT NULL DEFAULT 0,
    direction_agreement TEXT,
    catalyst_category TEXT,
    raw_json TEXT,
    FOREIGN KEY (shock_id) REFERENCES market_events_replay_shocks(id),
    UNIQUE(shock_id, context_source, context_record_id, window_sec)
);

CREATE TABLE IF NOT EXISTS market_events_replay_ai_critic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shock_id INTEGER NOT NULL,
    interpretation TEXT NOT NULL,
    continuation_prob REAL,
    reversal_prob REAL,
    confidence REAL,
    recommended_action TEXT NOT NULL,
    structured_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (shock_id) REFERENCES market_events_replay_shocks(id),
    UNIQUE(shock_id)
);
"""

E5_DDL = """
CREATE TABLE IF NOT EXISTS market_events_opportunity_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    score REAL NOT NULL,
    components_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_ai_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    prediction_json TEXT NOT NULL,
    reality_json TEXT NOT NULL,
    verdict TEXT NOT NULL,
    compared_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_digest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    message_text TEXT,
    sent INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(digest_type, period_key)
);

CREATE TABLE IF NOT EXISTS market_events_timeline_cache (
    event_id INTEGER PRIMARY KEY,
    timeline_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_scheduler_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_heartbeat_telegram_ts INTEGER,
    last_daily_digest_ts INTEGER,
    last_weekly_digest_ts INTEGER,
    updated_at INTEGER NOT NULL
);

INSERT OR IGNORE INTO market_events_scheduler_state (id, updated_at) VALUES (1, 0);
"""

E53_DDL = """
CREATE TABLE IF NOT EXISTS market_event_telegram_delivery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL DEFAULT 0,
    alert_type TEXT NOT NULL,
    chat_id TEXT,
    message_preview TEXT,
    status TEXT NOT NULL,
    latency_ms REAL,
    http_code INTEGER,
    telegram_message_id INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_tg_delivery_created
  ON market_event_telegram_delivery_log(created_at);
CREATE INDEX IF NOT EXISTS idx_me_tg_delivery_status
  ON market_event_telegram_delivery_log(status);
CREATE INDEX IF NOT EXISTS idx_me_tg_delivery_alert_type
  ON market_event_telegram_delivery_log(alert_type);
"""

E531_ALTER_STATEMENTS = (
    "ALTER TABLE market_event_telegram_delivery_log ADD COLUMN message_text TEXT",
)

F0_DDL = """
CREATE TABLE IF NOT EXISTS market_events_multitimeframe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detector_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    window_minutes INTEGER NOT NULL,
    return_pct REAL NOT NULL,
    atr_multiple REAL,
    volume_multiple REAL,
    direction TEXT NOT NULL,
    source_event_id INTEGER,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_mtf_detector ON market_events_multitimeframe(detector_id);
CREATE INDEX IF NOT EXISTS idx_me_mtf_symbol_ts ON market_events_multitimeframe(symbol, event_ts);

CREATE TABLE IF NOT EXISTS market_events_exhaustion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'TREND_EXHAUSTION',
    exhaustion_score REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    consecutive_candles INTEGER,
    cumulative_return_pct REAL,
    atr_expansion REAL,
    volume_expansion REAL,
    vwap_distance_pct REAL,
    ema20_distance_pct REAL,
    ema50_distance_pct REAL,
    source_event_id INTEGER,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_exhaustion_symbol ON market_events_exhaustion(symbol, event_ts);

CREATE TABLE IF NOT EXISTS market_event_exchange_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_symbol TEXT NOT NULL UNIQUE,
    resolved_venue TEXT,
    resolved_symbol TEXT,
    status TEXT NOT NULL,
    exchange_attempts_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_event_exchange_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    funding REAL,
    open_interest REAL,
    long_short_ratio REAL,
    volume_24h REAL,
    spread_bps REAL,
    vwap REAL,
    atr REAL,
    ema20_distance_pct REAL,
    ema50_distance_pct REAL,
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, venue),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_opportunity_scores_v2 (
    event_id INTEGER PRIMARY KEY,
    score REAL NOT NULL,
    score_breakdown_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_event_ai_analyses_f0 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    job_id INTEGER,
    prompt_version TEXT NOT NULL,
    response_json TEXT NOT NULL,
    bias TEXT,
    confidence REAL,
    continuation_probability REAL,
    reversal_probability REAL,
    summary_ru TEXT,
    summary_en TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_signal_ranking_weekly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_key TEXT NOT NULL UNIQUE,
    ranking_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_mtf_paper_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mtf_signal_id INTEGER NOT NULL,
    detector_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    entry_ts INTEGER,
    exit_ts INTEGER,
    pnl_pct REAL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (mtf_signal_id) REFERENCES market_events_multitimeframe(id)
);

CREATE TABLE IF NOT EXISTS market_events_mtf_replay_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_tag TEXT NOT NULL,
    detector_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_ts INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(run_tag, detector_id, symbol, signal_ts)
);
"""

F1_DDL = """
CREATE TABLE IF NOT EXISTS market_events_signal_reports_f1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    confidence_score REAL NOT NULL,
    confidence_breakdown_json TEXT NOT NULL,
    reversal_probability REAL NOT NULL,
    explanation_json TEXT NOT NULL,
    entry_recommendation TEXT NOT NULL,
    expected_target_pct REAL NOT NULL,
    expected_stop_pct REAL NOT NULL,
    matching_events_json TEXT NOT NULL,
    historical_reversal_rate REAL NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_sig_f1_event ON market_events_signal_reports_f1(event_id);

CREATE TABLE IF NOT EXISTS market_events_signal_outcomes_f1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    paper_run_id INTEGER,
    confidence_score REAL,
    entry_recommendation TEXT,
    expected_target_pct REAL,
    realized_pnl_pct REAL,
    holding_seconds INTEGER,
    ai_agreed INTEGER,
    historical_matched INTEGER,
    outcome_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_sig_out_f1_event ON market_events_signal_outcomes_f1(event_id);
"""

F2_DDL = """
CREATE TABLE IF NOT EXISTS market_events_funding_oi_history_f2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    venue TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    funding REAL,
    open_interest REAL,
    funding_delta REAL,
    oi_delta REAL,
    funding_regime TEXT,
    oi_regime TEXT,
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id),
    UNIQUE(event_id, venue, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_me_foi_f2_event ON market_events_funding_oi_history_f2(event_id);

CREATE TABLE IF NOT EXISTS market_events_signal_reports_f2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    exchange_consensus TEXT NOT NULL,
    exchange_detail_json TEXT NOT NULL,
    funding_regime TEXT,
    oi_regime TEXT,
    rvol_20 REAL,
    rvol_100 REAL,
    vwap_deviation_pct REAL,
    volume_label TEXT,
    market_structure_json TEXT NOT NULL,
    market_structure_labels TEXT NOT NULL,
    atr_percentile REAL,
    atr_expansion REAL,
    atr_exhaustion INTEGER,
    correlation_snapshot_json TEXT NOT NULL,
    correlation_verdict TEXT NOT NULL,
    historical_count INTEGER NOT NULL,
    historical_reversal_count INTEGER NOT NULL,
    historical_reversal_rate REAL NOT NULL,
    historical_similarity_json TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    confidence_breakdown_json TEXT NOT NULL,
    reversal_probability REAL NOT NULL,
    entry_recommendation TEXT NOT NULL,
    expected_target_pct REAL NOT NULL,
    expected_stop_pct REAL NOT NULL,
    ai_summary_v2_ru TEXT NOT NULL,
    telegram_rendered TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_sig_f2_event ON market_events_signal_reports_f2(event_id);
"""

F3_TREND_DDL = """
CREATE TABLE IF NOT EXISTS market_events_trend_shock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    trend_class TEXT NOT NULL,
    window_minutes INTEGER NOT NULL,
    cumulative_return_pct REAL NOT NULL,
    accumulated_move_pct REAL NOT NULL,
    consecutive_bars INTEGER NOT NULL,
    atr_multiple REAL NOT NULL,
    volume_multiple REAL NOT NULL,
    trend_score REAL NOT NULL,
    detectors_json TEXT NOT NULL,
    source_event_id INTEGER,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_trend_shock_event ON market_events_trend_shock(event_id);
CREATE INDEX IF NOT EXISTS idx_me_trend_shock_symbol ON market_events_trend_shock(symbol, event_ts);

CREATE TABLE IF NOT EXISTS market_events_entry_stages_f3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    stage_reason TEXT NOT NULL,
    wait_r2 INTEGER NOT NULL,
    wait_r3 INTEGER NOT NULL,
    scale_after_r2_pct INTEGER,
    scale_after_r3_pct INTEGER,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_entry_stages_f3_event ON market_events_entry_stages_f3(event_id);

CREATE TABLE IF NOT EXISTS market_events_alert_rankings_f3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    rank_score REAL NOT NULL,
    rank_position INTEGER NOT NULL,
    window_bucket INTEGER NOT NULL,
    alerted INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, window_bucket)
);

CREATE INDEX IF NOT EXISTS idx_me_alert_rank_f3_bucket ON market_events_alert_rankings_f3(window_bucket, rank_score DESC);
"""

F4_DDL = """
CREATE TABLE IF NOT EXISTS market_events_visual_intel_f4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    has_image INTEGER NOT NULL DEFAULT 0,
    extracted_json TEXT NOT NULL,
    chart_analysis_json TEXT NOT NULL,
    crosscheck_json TEXT NOT NULL,
    structured_json TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_visual_f4_event ON market_events_visual_intel_f4(event_id);

CREATE TABLE IF NOT EXISTS market_events_trend_shock_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    stage TEXT NOT NULL,
    window_minutes INTEGER NOT NULL,
    direction TEXT NOT NULL,
    cumulative_return_pct REAL NOT NULL,
    accumulated_move_pct REAL NOT NULL,
    consecutive_bars INTEGER NOT NULL,
    acceleration_ratio REAL NOT NULL,
    atr_multiple REAL NOT NULL,
    volume_multiple REAL NOT NULL,
    funding REAL,
    open_interest_delta REAL,
    liquidations_score REAL,
    trend_score REAL NOT NULL,
    continuation_probability REAL NOT NULL,
    detectors_json TEXT NOT NULL,
    source_event_id INTEGER,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_trend_v2_event ON market_events_trend_shock_v2(event_id);
CREATE INDEX IF NOT EXISTS idx_me_trend_v2_symbol ON market_events_trend_shock_v2(symbol, event_ts);
"""

F41_ALTER_STATEMENTS = (
    "ALTER TABLE market_event_alert_log ADD COLUMN message_type TEXT",
    "ALTER TABLE market_event_alert_log ADD COLUMN telegram_message_stage TEXT DEFAULT 'NONE'",
    "ALTER TABLE market_event_alert_log ADD COLUMN duplicate_prevented INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_me_alert_event_msgtype ON market_event_alert_log(event_id, message_type)",
)

F5_DDL = """
CREATE TABLE IF NOT EXISTS market_events_signal_reports_f5 (
    event_id INTEGER PRIMARY KEY,
    dynamic_confidence REAL NOT NULL,
    reversal_probability REAL NOT NULL,
    continuation_probability REAL NOT NULL,
    entry_quality TEXT NOT NULL,
    signal_cause TEXT NOT NULL,
    interest_factors_json TEXT NOT NULL,
    historical_examples_json TEXT NOT NULL,
    risk_reward REAL NOT NULL,
    tp_probabilities_json TEXT NOT NULL,
    tp1_pct REAL NOT NULL,
    tp2_pct REAL NOT NULL,
    tp3_pct REAL NOT NULL,
    stop_pct REAL NOT NULL,
    priority_score REAL NOT NULL,
    telegram_eligible INTEGER NOT NULL DEFAULT 0,
    telegram_skip_reason TEXT,
    ai_summary_ru TEXT NOT NULL,
    telegram_rendered TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_signal_f5_conf ON market_events_signal_reports_f5(dynamic_confidence DESC);
CREATE INDEX IF NOT EXISTS idx_me_signal_f5_quality ON market_events_signal_reports_f5(entry_quality);

CREATE TABLE IF NOT EXISTS market_events_signal_priority_f5 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    dynamic_confidence REAL NOT NULL,
    priority_score REAL NOT NULL,
    rank_position INTEGER NOT NULL DEFAULT 0,
    window_bucket INTEGER NOT NULL,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    telegram_skipped_reason TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, window_bucket),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_priority_f5_window ON market_events_signal_priority_f5(window_bucket, priority_score DESC);
"""

F51_DDL = """
CREATE TABLE IF NOT EXISTS market_events_signal_trace_f51 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    message_id INTEGER,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    latency_ms INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_trace_f51_event ON market_events_signal_trace_f51(event_id, created_at);
CREATE INDEX IF NOT EXISTS idx_me_trace_f51_stage ON market_events_signal_trace_f51(stage);
"""

F6_DDL = """
CREATE TABLE IF NOT EXISTS market_events_trader_performance_f6 (
    channel_name TEXT PRIMARY KEY,
    signals_count INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0,
    avg_rr REAL NOT NULL DEFAULT 0,
    avg_pnl REAL NOT NULL DEFAULT 0,
    max_drawdown REAL NOT NULL DEFAULT 0,
    avg_tp_time_sec REAL NOT NULL DEFAULT 0,
    author_score REAL NOT NULL DEFAULT 0,
    last_30_wins INTEGER NOT NULL DEFAULT 0,
    last_30_losses INTEGER NOT NULL DEFAULT 0,
    recent_outcomes_json TEXT NOT NULL DEFAULT '[]',
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_trader_f6_wr ON market_events_trader_performance_f6(win_rate DESC);
CREATE INDEX IF NOT EXISTS idx_me_trader_f6_score ON market_events_trader_performance_f6(author_score DESC);
"""

F7_DDL = """
CREATE TABLE IF NOT EXISTS market_events_market_intelligence_f7 (
    event_id INTEGER PRIMARY KEY,
    market_score REAL NOT NULL DEFAULT 0,
    component_scores_json TEXT NOT NULL DEFAULT '{}',
    final_confidence REAL NOT NULL DEFAULT 0,
    success_probability REAL NOT NULL DEFAULT 0,
    liquidation_regime TEXT,
    liquidation_continuation_prob REAL,
    liquidation_reversal_prob REAL,
    liquidation_intel_json TEXT NOT NULL DEFAULT '{}',
    dominance_regime TEXT,
    dominance_json TEXT NOT NULL DEFAULT '{}',
    whale_score REAL NOT NULL DEFAULT 0,
    whale_intel_json TEXT NOT NULL DEFAULT '{}',
    news_impact TEXT NOT NULL DEFAULT 'LOW',
    news_keywords_json TEXT NOT NULL DEFAULT '[]',
    long_trend_stage TEXT,
    long_trend_json TEXT NOT NULL DEFAULT '{}',
    image_intel_json TEXT NOT NULL DEFAULT '{}',
    interest_factors_json TEXT NOT NULL DEFAULT '[]',
    telegram_rendered TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_intel_f7_score ON market_events_market_intelligence_f7(market_score DESC);
CREATE INDEX IF NOT EXISTS idx_me_intel_f7_conf ON market_events_market_intelligence_f7(final_confidence DESC);
"""
