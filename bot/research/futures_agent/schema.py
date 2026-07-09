"""Futures agent schema — additive tables only."""

from __future__ import annotations

from typing import Any

from bot.research.futures_agent.db import connection_is_postgres
from bot.research.futures_agent.schema_validate import (
    validate_stage1_schema,
    validate_stage2_schema,
    validate_stage3_schema,
    validate_stage4_schema,
    validate_stage5_schema,
)

MIGRATIONS_TABLE = "futures_agent_migrations"
STAGE1_VERSION = 1
STAGE2_VERSION = 2
STAGE3_VERSION = 3
STAGE4_VERSION = 4
STAGE5_VERSION = 5

STAGE1_DDL = """
CREATE TABLE IF NOT EXISTS futures_agent_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS futures_agent_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'forwarded',
    telegram_message_id TEXT,
    raw_message_id BIGINT,
    raw_text TEXT NOT NULL,
    received_at INTEGER NOT NULL,
    input_type TEXT NOT NULL DEFAULT 'forwarded',
    processing_status TEXT NOT NULL DEFAULT 'received',
    status_detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, telegram_message_id)
);

CREATE INDEX IF NOT EXISTS idx_fa_inputs_status ON futures_agent_inputs(processing_status);
CREATE INDEX IF NOT EXISTS idx_fa_inputs_received ON futures_agent_inputs(received_at);

CREATE TABLE IF NOT EXISTS futures_agent_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_id INTEGER NOT NULL UNIQUE,
    parser_version TEXT NOT NULL,
    taxonomy TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    entry_low REAL,
    entry_high REAL,
    stop_loss REAL,
    leverage REAL,
    timeframe TEXT,
    explicit_confidence REAL,
    parse_status TEXT NOT NULL,
    passes_gate INTEGER NOT NULL DEFAULT 0,
    gate_reason TEXT,
    parse_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (input_id) REFERENCES futures_agent_inputs(id)
);

CREATE INDEX IF NOT EXISTS idx_fa_signals_symbol ON futures_agent_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_fa_signals_taxonomy ON futures_agent_signals(taxonomy);

CREATE TABLE IF NOT EXISTS futures_agent_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    target_index INTEGER NOT NULL,
    target_price REAL NOT NULL,
    UNIQUE(signal_id, target_index),
    FOREIGN KEY (signal_id) REFERENCES futures_agent_signals(id)
);
"""

STAGE1_DDL_POSTGRES = """
CREATE TABLE IF NOT EXISTS futures_agent_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS futures_agent_inputs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'forwarded',
    telegram_message_id TEXT,
    raw_message_id BIGINT,
    raw_text TEXT NOT NULL,
    received_at BIGINT NOT NULL,
    input_type TEXT NOT NULL DEFAULT 'forwarded',
    processing_status TEXT NOT NULL DEFAULT 'received',
    status_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source, telegram_message_id)
);

CREATE INDEX IF NOT EXISTS idx_fa_inputs_status ON futures_agent_inputs(processing_status);
CREATE INDEX IF NOT EXISTS idx_fa_inputs_received ON futures_agent_inputs(received_at);

CREATE TABLE IF NOT EXISTS futures_agent_signals (
    id BIGSERIAL PRIMARY KEY,
    input_id BIGINT NOT NULL UNIQUE REFERENCES futures_agent_inputs(id),
    parser_version TEXT NOT NULL,
    taxonomy TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    entry_low DOUBLE PRECISION,
    entry_high DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION,
    leverage DOUBLE PRECISION,
    timeframe TEXT,
    explicit_confidence DOUBLE PRECISION,
    parse_status TEXT NOT NULL,
    passes_gate BOOLEAN NOT NULL DEFAULT FALSE,
    gate_reason TEXT,
    parse_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fa_signals_symbol ON futures_agent_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_fa_signals_taxonomy ON futures_agent_signals(taxonomy);

CREATE TABLE IF NOT EXISTS futures_agent_targets (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT NOT NULL REFERENCES futures_agent_signals(id),
    target_index INTEGER NOT NULL,
    target_price DOUBLE PRECISION NOT NULL,
    UNIQUE(signal_id, target_index)
);
"""

STAGE2_DDL = """
CREATE TABLE IF NOT EXISTS futures_agent_market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    snapshot_ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'binance',
    spot_price REAL,
    futures_price REAL,
    return_1m REAL,
    return_5m REAL,
    return_15m REAL,
    return_30m REAL,
    return_1h REAL,
    return_4h REAL,
    return_24h REAL,
    ema_fast REAL,
    ema_slow REAL,
    ema_slope REAL,
    atr REAL,
    realized_vol REAL,
    distance_from_local_high REAL,
    distance_from_local_low REAL,
    volume_ratio REAL,
    funding_rate REAL,
    open_interest REAL,
    basis REAL,
    data_quality TEXT NOT NULL,
    raw_metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(signal_id, symbol),
    FOREIGN KEY (signal_id) REFERENCES futures_agent_signals(id)
);

CREATE INDEX IF NOT EXISTS idx_fa_snap_signal ON futures_agent_market_snapshots(signal_id);

CREATE TABLE IF NOT EXISTS futures_agent_btc_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL UNIQUE,
    snapshot_ts INTEGER NOT NULL,
    btc_price REAL,
    return_5m REAL,
    return_15m REAL,
    return_1h REAL,
    return_4h REAL,
    return_24h REAL,
    trend_15m TEXT,
    trend_1h TEXT,
    trend_4h TEXT,
    volatility_regime TEXT,
    momentum_regime TEXT,
    market_regime TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (signal_id) REFERENCES futures_agent_signals(id)
);

CREATE TABLE IF NOT EXISTS futures_agent_relative_strength (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    snapshot_ts INTEGER NOT NULL,
    alt_return_5m REAL,
    alt_return_15m REAL,
    alt_return_1h REAL,
    btc_return_5m REAL,
    btc_return_15m REAL,
    btc_return_1h REAL,
    excess_return_5m REAL,
    excess_return_15m REAL,
    excess_return_1h REAL,
    correlation_to_btc REAL,
    beta_to_btc REAL,
    relative_strength_label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (signal_id) REFERENCES futures_agent_signals(id)
);
"""

STAGE2_DDL_POSTGRES = """
CREATE TABLE IF NOT EXISTS futures_agent_market_snapshots (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT NOT NULL REFERENCES futures_agent_signals(id),
    snapshot_ts BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'binance',
    spot_price DOUBLE PRECISION,
    futures_price DOUBLE PRECISION,
    return_1m DOUBLE PRECISION,
    return_5m DOUBLE PRECISION,
    return_15m DOUBLE PRECISION,
    return_30m DOUBLE PRECISION,
    return_1h DOUBLE PRECISION,
    return_4h DOUBLE PRECISION,
    return_24h DOUBLE PRECISION,
    ema_fast DOUBLE PRECISION,
    ema_slow DOUBLE PRECISION,
    ema_slope DOUBLE PRECISION,
    atr DOUBLE PRECISION,
    realized_vol DOUBLE PRECISION,
    distance_from_local_high DOUBLE PRECISION,
    distance_from_local_low DOUBLE PRECISION,
    volume_ratio DOUBLE PRECISION,
    funding_rate DOUBLE PRECISION,
    open_interest DOUBLE PRECISION,
    basis DOUBLE PRECISION,
    data_quality TEXT NOT NULL,
    raw_metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(signal_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_fa_snap_signal ON futures_agent_market_snapshots(signal_id);

CREATE TABLE IF NOT EXISTS futures_agent_btc_context (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT NOT NULL UNIQUE REFERENCES futures_agent_signals(id),
    snapshot_ts BIGINT NOT NULL,
    btc_price DOUBLE PRECISION,
    return_5m DOUBLE PRECISION,
    return_15m DOUBLE PRECISION,
    return_1h DOUBLE PRECISION,
    return_4h DOUBLE PRECISION,
    return_24h DOUBLE PRECISION,
    trend_15m TEXT,
    trend_1h TEXT,
    trend_4h TEXT,
    volatility_regime TEXT,
    momentum_regime TEXT,
    market_regime TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS futures_agent_relative_strength (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT NOT NULL UNIQUE REFERENCES futures_agent_signals(id),
    symbol TEXT NOT NULL,
    snapshot_ts BIGINT NOT NULL,
    alt_return_5m DOUBLE PRECISION,
    alt_return_15m DOUBLE PRECISION,
    alt_return_1h DOUBLE PRECISION,
    btc_return_5m DOUBLE PRECISION,
    btc_return_15m DOUBLE PRECISION,
    btc_return_1h DOUBLE PRECISION,
    excess_return_5m DOUBLE PRECISION,
    excess_return_15m DOUBLE PRECISION,
    excess_return_1h DOUBLE PRECISION,
    correlation_to_btc DOUBLE PRECISION,
    beta_to_btc DOUBLE PRECISION,
    relative_strength_label TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

STAGE3_DDL = """
CREATE TABLE IF NOT EXISTS futures_agent_trader_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    message_ts INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_type TEXT NOT NULL,
    symbols_json TEXT NOT NULL DEFAULT '[]',
    deterministic_confidence REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel_name, source_message_id)
);

CREATE INDEX IF NOT EXISTS idx_fa_trader_posts_ts ON futures_agent_trader_posts(message_ts);
CREATE INDEX IF NOT EXISTS idx_fa_trader_posts_channel ON futures_agent_trader_posts(channel_name);
CREATE INDEX IF NOT EXISTS idx_fa_trader_posts_type ON futures_agent_trader_posts(content_type);
CREATE INDEX IF NOT EXISTS idx_fa_trader_posts_hash ON futures_agent_trader_posts(content_hash);

CREATE TABLE IF NOT EXISTS futures_agent_trader_theses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    symbol TEXT,
    direction TEXT NOT NULL,
    thesis_text TEXT NOT NULL,
    horizon TEXT,
    condition_text TEXT,
    invalidation_text TEXT,
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (post_id) REFERENCES futures_agent_trader_posts(id)
);

CREATE INDEX IF NOT EXISTS idx_fa_trader_theses_post ON futures_agent_trader_theses(post_id);
CREATE INDEX IF NOT EXISTS idx_fa_trader_theses_symbol ON futures_agent_trader_theses(symbol);

CREATE TABLE IF NOT EXISTS futures_agent_trader_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER NOT NULL,
    level_type TEXT NOT NULL,
    price REAL NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY (thesis_id) REFERENCES futures_agent_trader_theses(id)
);

CREATE INDEX IF NOT EXISTS idx_fa_trader_levels_thesis ON futures_agent_trader_levels(thesis_id);

CREATE TABLE IF NOT EXISTS futures_agent_thesis_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER NOT NULL,
    evaluation_horizon TEXT NOT NULL,
    price_at_thesis REAL,
    mfe_pct REAL,
    mae_pct REAL,
    return_pct REAL,
    direction_correct INTEGER,
    target_hit INTEGER,
    stop_hit INTEGER,
    evaluated_at INTEGER NOT NULL,
    UNIQUE(thesis_id, evaluation_horizon),
    FOREIGN KEY (thesis_id) REFERENCES futures_agent_trader_theses(id)
);

CREATE TABLE IF NOT EXISTS futures_agent_source_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    symbol_group TEXT,
    horizon TEXT NOT NULL DEFAULT 'all',
    sample_size INTEGER NOT NULL DEFAULT 0,
    directional_accuracy REAL,
    avg_mfe REAL,
    avg_mae REAL,
    expectancy_proxy REAL,
    wilson_lower_bound REAL,
    recency_weighted_score REAL,
    calculated_as_of INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel_name, content_type, symbol_group, horizon)
);
"""

STAGE3_DDL_POSTGRES = """
CREATE TABLE IF NOT EXISTS futures_agent_trader_posts (
    id BIGSERIAL PRIMARY KEY,
    source_message_id TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    message_ts BIGINT NOT NULL,
    raw_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_type TEXT NOT NULL,
    symbols_json JSONB NOT NULL DEFAULT '[]',
    deterministic_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(channel_name, source_message_id)
);

CREATE INDEX IF NOT EXISTS idx_fa_trader_posts_ts ON futures_agent_trader_posts(message_ts);
CREATE INDEX IF NOT EXISTS idx_fa_trader_posts_channel ON futures_agent_trader_posts(channel_name);
CREATE INDEX IF NOT EXISTS idx_fa_trader_posts_type ON futures_agent_trader_posts(content_type);
CREATE INDEX IF NOT EXISTS idx_fa_trader_posts_hash ON futures_agent_trader_posts(content_hash);

CREATE TABLE IF NOT EXISTS futures_agent_trader_theses (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES futures_agent_trader_posts(id),
    symbol TEXT,
    direction TEXT NOT NULL,
    thesis_text TEXT NOT NULL,
    horizon TEXT,
    condition_text TEXT,
    invalidation_text TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fa_trader_theses_post ON futures_agent_trader_theses(post_id);
CREATE INDEX IF NOT EXISTS idx_fa_trader_theses_symbol ON futures_agent_trader_theses(symbol);

CREATE TABLE IF NOT EXISTS futures_agent_trader_levels (
    id BIGSERIAL PRIMARY KEY,
    thesis_id BIGINT NOT NULL REFERENCES futures_agent_trader_theses(id),
    level_type TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_fa_trader_levels_thesis ON futures_agent_trader_levels(thesis_id);

CREATE TABLE IF NOT EXISTS futures_agent_thesis_outcomes (
    id BIGSERIAL PRIMARY KEY,
    thesis_id BIGINT NOT NULL REFERENCES futures_agent_trader_theses(id),
    evaluation_horizon TEXT NOT NULL,
    price_at_thesis DOUBLE PRECISION,
    mfe_pct DOUBLE PRECISION,
    mae_pct DOUBLE PRECISION,
    return_pct DOUBLE PRECISION,
    direction_correct INTEGER,
    target_hit INTEGER,
    stop_hit INTEGER,
    evaluated_at BIGINT NOT NULL,
    UNIQUE(thesis_id, evaluation_horizon)
);

CREATE TABLE IF NOT EXISTS futures_agent_source_scores (
    id BIGSERIAL PRIMARY KEY,
    channel_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    symbol_group TEXT,
    horizon TEXT NOT NULL DEFAULT 'all',
    sample_size INTEGER NOT NULL DEFAULT 0,
    directional_accuracy DOUBLE PRECISION,
    avg_mfe DOUBLE PRECISION,
    avg_mae DOUBLE PRECISION,
    expectancy_proxy DOUBLE PRECISION,
    wilson_lower_bound DOUBLE PRECISION,
    recency_weighted_score DOUBLE PRECISION,
    calculated_as_of BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(channel_name, content_type, symbol_group, horizon)
);
"""

STAGE4_DDL = """
-- Stage 4: Thesis outcome engine + richer source scores

-- Add missing outcome fields (SQLite)
ALTER TABLE futures_agent_thesis_outcomes ADD COLUMN time_to_target_sec INTEGER;
ALTER TABLE futures_agent_thesis_outcomes ADD COLUMN time_to_stop_sec INTEGER;
ALTER TABLE futures_agent_thesis_outcomes ADD COLUMN final_outcome TEXT;

-- New table for Phase C source intelligence (SQLite)
CREATE TABLE IF NOT EXISTS futures_agent_source_scores_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    timeframe TEXT,
    horizon TEXT NOT NULL DEFAULT 'all',
    sample_size INTEGER NOT NULL DEFAULT 0,
    win_rate REAL,
    avg_return REAL,
    avg_mfe REAL,
    avg_mae REAL,
    profit_factor REAL,
    sharpe REAL,
    bayesian_mean REAL,
    wilson_lower_bound REAL,
    recency_weighted_score REAL,
    calculated_as_of INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel_name, content_type, symbol, direction, timeframe, horizon)
);
CREATE INDEX IF NOT EXISTS idx_fa_scores_v2_channel ON futures_agent_source_scores_v2(channel_name);
CREATE INDEX IF NOT EXISTS idx_fa_scores_v2_symbol ON futures_agent_source_scores_v2(symbol);
"""

STAGE4_DDL_POSTGRES = """
-- Stage 4: Thesis outcome engine + richer source scores

ALTER TABLE futures_agent_thesis_outcomes
  ADD COLUMN IF NOT EXISTS time_to_target_sec INTEGER;
ALTER TABLE futures_agent_thesis_outcomes
  ADD COLUMN IF NOT EXISTS time_to_stop_sec INTEGER;
ALTER TABLE futures_agent_thesis_outcomes
  ADD COLUMN IF NOT EXISTS final_outcome TEXT;

CREATE TABLE IF NOT EXISTS futures_agent_source_scores_v2 (
    id BIGSERIAL PRIMARY KEY,
    channel_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    timeframe TEXT,
    horizon TEXT NOT NULL DEFAULT 'all',
    sample_size INTEGER NOT NULL DEFAULT 0,
    win_rate DOUBLE PRECISION,
    avg_return DOUBLE PRECISION,
    avg_mfe DOUBLE PRECISION,
    avg_mae DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    sharpe DOUBLE PRECISION,
    bayesian_mean DOUBLE PRECISION,
    wilson_lower_bound DOUBLE PRECISION,
    recency_weighted_score DOUBLE PRECISION,
    calculated_as_of BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(channel_name, content_type, symbol, direction, timeframe, horizon)
);
CREATE INDEX IF NOT EXISTS idx_fa_scores_v2_channel ON futures_agent_source_scores_v2(channel_name);
CREATE INDEX IF NOT EXISTS idx_fa_scores_v2_symbol ON futures_agent_source_scores_v2(symbol);
"""

STAGE5_DDL = """
-- Stage 5: Phase D.1 historical signal outcome engine

CREATE TABLE IF NOT EXISTS futures_agent_research_market_data_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_ts INTEGER NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    close_price REAL NOT NULL,
    data_source TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    UNIQUE(exchange_symbol, interval, open_ts)
);
CREATE INDEX IF NOT EXISTS idx_fa_rmdc_symbol_ts
  ON futures_agent_research_market_data_cache(exchange_symbol, interval, open_ts);

CREATE TABLE IF NOT EXISTS futures_agent_research_signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    symbol TEXT,
    exchange_symbol TEXT,
    direction TEXT NOT NULL,
    decision_ts INTEGER NOT NULL,
    entry_mode TEXT NOT NULL,
    entry_status TEXT NOT NULL,
    entry_ts INTEGER,
    entry_price REAL,
    entry_fill_model TEXT,
    stop_mode TEXT NOT NULL,
    stop_price REAL,
    outcome_status TEXT NOT NULL,
    first_terminal_event TEXT,
    first_terminal_ts INTEGER,
    max_target_reached INTEGER NOT NULL DEFAULT 0,
    mfe_pct REAL,
    mae_pct REAL,
    ambiguous_intrabar INTEGER NOT NULL DEFAULT 0,
    conservative_terminal TEXT,
    optimistic_terminal TEXT,
    raw_return_pct REAL,
    data_quality_status TEXT NOT NULL,
    symbol_resolve_status TEXT,
    candle_meta_json TEXT,
    policy_results_json TEXT,
    engine_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(thesis_id, engine_version),
    FOREIGN KEY (thesis_id) REFERENCES futures_agent_trader_theses(id),
    FOREIGN KEY (post_id) REFERENCES futures_agent_trader_posts(id)
);
CREATE INDEX IF NOT EXISTS idx_fa_rso_channel ON futures_agent_research_signal_outcomes(channel);
CREATE INDEX IF NOT EXISTS idx_fa_rso_decision_ts ON futures_agent_research_signal_outcomes(decision_ts);

CREATE TABLE IF NOT EXISTS futures_agent_research_signal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    target_index INTEGER,
    event_ts INTEGER NOT NULL,
    event_price REAL NOT NULL,
    candle_open_ts INTEGER NOT NULL,
    ambiguity_flag INTEGER NOT NULL DEFAULT 0,
    ordinal INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (outcome_id) REFERENCES futures_agent_research_signal_outcomes(id)
);
CREATE INDEX IF NOT EXISTS idx_fa_rse_outcome ON futures_agent_research_signal_events(outcome_id);

CREATE TABLE IF NOT EXISTS futures_agent_research_signal_markouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_id INTEGER NOT NULL,
    horizon TEXT NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    mark_ts INTEGER NOT NULL,
    mark_price REAL NOT NULL,
    directional_return_pct REAL NOT NULL,
    mfe_pct REAL NOT NULL,
    mae_pct REAL NOT NULL,
    UNIQUE(outcome_id, horizon),
    FOREIGN KEY (outcome_id) REFERENCES futures_agent_research_signal_outcomes(id)
);
"""

STAGE5_DDL_POSTGRES = """
CREATE TABLE IF NOT EXISTS futures_agent_research_market_data_cache (
    id BIGSERIAL PRIMARY KEY,
    exchange_symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_ts BIGINT NOT NULL,
    open_price DOUBLE PRECISION NOT NULL,
    high_price DOUBLE PRECISION NOT NULL,
    low_price DOUBLE PRECISION NOT NULL,
    close_price DOUBLE PRECISION NOT NULL,
    data_source TEXT NOT NULL,
    fetched_at BIGINT NOT NULL,
    UNIQUE(exchange_symbol, interval, open_ts)
);
CREATE INDEX IF NOT EXISTS idx_fa_rmdc_symbol_ts
  ON futures_agent_research_market_data_cache(exchange_symbol, interval, open_ts);

CREATE TABLE IF NOT EXISTS futures_agent_research_signal_outcomes (
    id BIGSERIAL PRIMARY KEY,
    thesis_id BIGINT NOT NULL REFERENCES futures_agent_trader_theses(id),
    post_id BIGINT NOT NULL REFERENCES futures_agent_trader_posts(id),
    channel TEXT NOT NULL,
    symbol TEXT,
    exchange_symbol TEXT,
    direction TEXT NOT NULL,
    decision_ts BIGINT NOT NULL,
    entry_mode TEXT NOT NULL,
    entry_status TEXT NOT NULL,
    entry_ts BIGINT,
    entry_price DOUBLE PRECISION,
    entry_fill_model TEXT,
    stop_mode TEXT NOT NULL,
    stop_price DOUBLE PRECISION,
    outcome_status TEXT NOT NULL,
    first_terminal_event TEXT,
    first_terminal_ts BIGINT,
    max_target_reached INTEGER NOT NULL DEFAULT 0,
    mfe_pct DOUBLE PRECISION,
    mae_pct DOUBLE PRECISION,
    ambiguous_intrabar INTEGER NOT NULL DEFAULT 0,
    conservative_terminal TEXT,
    optimistic_terminal TEXT,
    raw_return_pct DOUBLE PRECISION,
    data_quality_status TEXT NOT NULL,
    symbol_resolve_status TEXT,
    candle_meta_json TEXT,
    policy_results_json TEXT,
    engine_version TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(thesis_id, engine_version)
);
CREATE INDEX IF NOT EXISTS idx_fa_rso_channel ON futures_agent_research_signal_outcomes(channel);
CREATE INDEX IF NOT EXISTS idx_fa_rso_decision_ts ON futures_agent_research_signal_outcomes(decision_ts);

CREATE TABLE IF NOT EXISTS futures_agent_research_signal_events (
    id BIGSERIAL PRIMARY KEY,
    outcome_id BIGINT NOT NULL REFERENCES futures_agent_research_signal_outcomes(id),
    event_type TEXT NOT NULL,
    target_index INTEGER,
    event_ts BIGINT NOT NULL,
    event_price DOUBLE PRECISION NOT NULL,
    candle_open_ts BIGINT NOT NULL,
    ambiguity_flag INTEGER NOT NULL DEFAULT 0,
    ordinal INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fa_rse_outcome ON futures_agent_research_signal_events(outcome_id);

CREATE TABLE IF NOT EXISTS futures_agent_research_signal_markouts (
    id BIGSERIAL PRIMARY KEY,
    outcome_id BIGINT NOT NULL REFERENCES futures_agent_research_signal_outcomes(id),
    horizon TEXT NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    mark_ts BIGINT NOT NULL,
    mark_price DOUBLE PRECISION NOT NULL,
    directional_return_pct DOUBLE PRECISION NOT NULL,
    mfe_pct DOUBLE PRECISION NOT NULL,
    mae_pct DOUBLE PRECISION NOT NULL,
    UNIQUE(outcome_id, horizon)
);
"""


def apply_migrations(conn: Any) -> list[str]:
    """Apply Stage 1 + Stage 2 + Stage 3 migrations idempotently."""
    postgres = connection_is_postgres(conn)
    applied: list[str] = []

    for stmt in _split_ddl(STAGE1_DDL_POSTGRES if postgres else STAGE1_DDL):
        conn.execute(stmt)

    if _has_migration(conn, STAGE1_VERSION):
        validation = validate_stage1_schema(conn)
        if not validation["valid"]:
            raise RuntimeError(f"Stage 1 schema validation failed: {validation['errors']}")
    else:
        validation = validate_stage1_schema(conn)
        if not validation["valid"]:
            raise RuntimeError(f"Stage 1 schema validation failed after DDL: {validation['errors']}")
        conn.execute(
            f"INSERT INTO {MIGRATIONS_TABLE} (version, description) VALUES (?, ?)",
            (STAGE1_VERSION, "stage1_core_inputs_signals_targets"),
        )
        applied.append(f"v{STAGE1_VERSION}: stage1_core")

    for stmt in _split_ddl(STAGE2_DDL_POSTGRES if postgres else STAGE2_DDL):
        conn.execute(stmt)

    if _has_migration(conn, STAGE2_VERSION):
        validation2 = validate_stage2_schema(conn)
        if not validation2["valid"]:
            raise RuntimeError(f"Stage 2 schema validation failed: {validation2['errors']}")
    else:
        validation2 = validate_stage2_schema(conn)
        if not validation2["valid"]:
            raise RuntimeError(f"Stage 2 schema validation failed after DDL: {validation2['errors']}")
        conn.execute(
            f"INSERT INTO {MIGRATIONS_TABLE} (version, description) VALUES (?, ?)",
            (STAGE2_VERSION, "stage2_market_snapshots_btc_context"),
        )
        applied.append(f"v{STAGE2_VERSION}: stage2_snapshots")

    for stmt in _split_ddl(STAGE3_DDL_POSTGRES if postgres else STAGE3_DDL):
        conn.execute(stmt)

    if _has_migration(conn, STAGE3_VERSION):
        validation3 = validate_stage3_schema(conn)
        if not validation3["valid"]:
            raise RuntimeError(f"Stage 3 schema validation failed: {validation3['errors']}")
    else:
        validation3 = validate_stage3_schema(conn)
        if not validation3["valid"]:
            raise RuntimeError(f"Stage 3 schema validation failed after DDL: {validation3['errors']}")
        conn.execute(
            f"INSERT INTO {MIGRATIONS_TABLE} (version, description) VALUES (?, ?)",
            (STAGE3_VERSION, "stage3_trader_research_posts_theses"),
        )
        applied.append(f"v{STAGE3_VERSION}: stage3_research")

    # Stage 4 (Phase C)
    if not _has_migration(conn, STAGE4_VERSION):
        if postgres:
            # Postgres DDL includes conditional ALTERs
            for stmt in _split_ddl(STAGE4_DDL_POSTGRES):
                conn.execute(stmt)
        else:
            # SQLite ALTER TABLE may fail if column exists; ignore "duplicate column" errors.
            for stmt in _split_ddl(STAGE4_DDL):
                try:
                    conn.execute(stmt)
                except Exception as exc:
                    msg = str(exc).lower()
                    if "duplicate column" in msg or "already exists" in msg:
                        continue
                    raise
        validation4 = validate_stage4_schema(conn)
        if not validation4["valid"]:
            raise RuntimeError(f"Stage 4 schema validation failed after DDL: {validation4['errors']}")
        conn.execute(
            f"INSERT INTO {MIGRATIONS_TABLE} (version, description) VALUES (?, ?)",
            (STAGE4_VERSION, "stage4_thesis_outcomes_and_source_scores_v2"),
        )
        applied.append(f"v{STAGE4_VERSION}: stage4_outcomes_scores")

    if not _has_migration(conn, STAGE5_VERSION):
        for stmt in _split_ddl(STAGE5_DDL_POSTGRES if postgres else STAGE5_DDL):
            conn.execute(stmt)
        validation5 = validate_stage5_schema(conn)
        if not validation5["valid"]:
            raise RuntimeError(f"Stage 5 schema validation failed after DDL: {validation5['errors']}")
        conn.execute(
            f"INSERT INTO {MIGRATIONS_TABLE} (version, description) VALUES (?, ?)",
            (STAGE5_VERSION, "stage5_research_signal_outcome_engine"),
        )
        applied.append(f"v{STAGE5_VERSION}: stage5_signal_outcomes")

    return applied


def _has_migration(conn: Any, version: int) -> bool:
    row = conn.execute(
        f"SELECT version FROM {MIGRATIONS_TABLE} WHERE version = ?",
        (version,),
    ).fetchone()
    return row is not None


def _split_ddl(ddl: str) -> list[str]:
    stmts: list[str] = []
    buf: list[str] = []
    for line in ddl.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmts.append("\n".join(buf))
            buf = []
    return stmts
