"""SQLite schema for Forward Validation Monitor V1."""

from __future__ import annotations

from typing import Any

TABLE = "forward_validation_v1"
STATE_TABLE = "forward_validation_state_v1"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    book TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    opened_at INTEGER,
    closed_at INTEGER,
    symbol TEXT,
    direction TEXT,
    decision TEXT,
    confidence REAL,
    brain_score REAL,
    timeline_similarity REAL,
    fingerprint_similarity REAL,
    historical_wr REAL,
    historical_pf REAL,
    historical_ev REAL,
    reality_score REAL,
    elite_score REAL,
    entry_price REAL,
    expected_stop REAL,
    expected_target REAL,
    result TEXT,
    pnl REAL,
    pnl_pct REAL,
    holding_time_sec REAL,
    mae REAL,
    mfe REAL,
    expected_wr REAL,
    actual_result TEXT,
    prediction_correct INTEGER,
    meta_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(trade_id, book)
);
CREATE INDEX IF NOT EXISTS idx_fv_v1_book_status ON {TABLE}(book, status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_fv_v1_opened ON {TABLE}(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_fv_v1_closed ON {TABLE}(closed_at DESC);

CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
    key TEXT PRIMARY KEY,
    value_text TEXT,
    value_real REAL,
    updated_at INTEGER NOT NULL
);
"""


def ensure_forward_validation_schema(conn: Any) -> None:
    conn.executescript(DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = ["DDL", "STATE_TABLE", "TABLE", "ensure_forward_validation_schema"]
