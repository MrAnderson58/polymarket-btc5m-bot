"""SQLite schema for Decision Error Learning Engine V1."""

from __future__ import annotations

from typing import Any

ERRORS_TABLE = "decision_error_learning_v1"
PATTERNS_TABLE = "decision_error_patterns_v1"

ERROR_DDL = f"""
CREATE TABLE IF NOT EXISTS {ERRORS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    symbol TEXT,
    opened_at INTEGER,
    confusion TEXT NOT NULL,
    error_class TEXT,
    book TEXT,
    pnl REAL,
    direction TEXT,
    decision TEXT,
    confidence REAL,
    primary_module TEXT,
    reasons_json TEXT,
    modules_json TEXT,
    recovered_ev_if_ignored REAL,
    created_at INTEGER NOT NULL,
    UNIQUE(trade_id, book)
);
CREATE INDEX IF NOT EXISTS idx_del_v1_class ON {ERRORS_TABLE}(error_class, pnl DESC);
CREATE INDEX IF NOT EXISTS idx_del_v1_module ON {ERRORS_TABLE}(primary_module, confusion);

CREATE TABLE IF NOT EXISTS {PATTERNS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    pattern TEXT NOT NULL,
    primary_module TEXT,
    n INTEGER NOT NULL DEFAULT 0,
    total_pnl REAL,
    mean_pnl REAL,
    wr REAL,
    recovered_ev REAL,
    meta_json TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dep_v1_kind ON {PATTERNS_TABLE}(kind, n DESC);
"""


def ensure_decision_error_schema(conn: Any) -> None:
    conn.executescript(ERROR_DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = [
    "ERROR_DDL",
    "ERRORS_TABLE",
    "PATTERNS_TABLE",
    "ensure_decision_error_schema",
]
