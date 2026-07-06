"""Futures agent schema — additive tables only."""

from __future__ import annotations

from typing import Any

from bot.research.futures_agent.schema_validate import validate_stage1_schema

MIGRATIONS_TABLE = "futures_agent_migrations"
STAGE1_VERSION = 1

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


def apply_migrations(conn: Any, *, postgres: bool = False) -> list[str]:
    """Create schema atomically; record migration only after DDL + validation."""
    ddl = STAGE1_DDL_POSTGRES if postgres else STAGE1_DDL

    for stmt in _split_ddl(ddl):
        conn.execute(stmt)

    row = conn.execute(
        f"SELECT version FROM {MIGRATIONS_TABLE} WHERE version = ?",
        (STAGE1_VERSION,),
    ).fetchone()
    if row:
        validation = validate_stage1_schema(conn, postgres=postgres)
        if not validation["valid"]:
            raise RuntimeError(f"Schema validation failed: {validation['errors']}")
        return []

    validation = validate_stage1_schema(conn, postgres=postgres)
    if not validation["valid"]:
        raise RuntimeError(f"Schema validation failed after DDL: {validation['errors']}")

    conn.execute(
        f"INSERT INTO {MIGRATIONS_TABLE} (version, description) VALUES (?, ?)",
        (STAGE1_VERSION, "stage1_core_inputs_signals_targets"),
    )
    return [f"v{STAGE1_VERSION}: stage1_core"]


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
