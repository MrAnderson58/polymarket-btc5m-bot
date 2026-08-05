"""SQLite schema for Paper Mathematics Validation V1."""

from __future__ import annotations

from typing import Any

BOOK_TABLE = "paper_math_book_v1"
RESULTS_TABLE = "paper_math_results_v1"
VALIDATION_TABLE = "paper_math_validation_v1"

DDL = f"""
CREATE TABLE IF NOT EXISTS {BOOK_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    book TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    symbol TEXT,
    direction TEXT,
    opened_at INTEGER,
    entry_px REAL,
    stop_px REAL,
    target_px REAL,
    decision TEXT,
    decision_rank TEXT,
    decision_score REAL,
    brain_score REAL,
    reality_score REAL,
    replay_similarity REAL,
    timeline_similarity REAL,
    fingerprint_similarity REAL,
    historical_wr REAL,
    historical_pf REAL,
    historical_ev REAL,
    sample_size INTEGER,
    why_entered TEXT,
    why_almost_rejected TEXT,
    expected_holding_time REAL,
    expected_drawdown REAL,
    regime TEXT,
    result TEXT,
    pnl REAL,
    rejection_reasons TEXT,
    meta_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(trade_id, book)
);
CREATE INDEX IF NOT EXISTS idx_pmb_v1_book ON {BOOK_TABLE}(book, accepted, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_pmb_v1_trade ON {BOOK_TABLE}(trade_id);

CREATE TABLE IF NOT EXISTS {RESULTS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    book TEXT NOT NULL,
    expected_wr REAL,
    actual_result TEXT,
    expected_ev REAL,
    actual_ev REAL,
    expected_dd REAL,
    actual_dd REAL,
    miss_reason TEXT,
    pnl REAL,
    meta_json TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(trade_id, book)
);
CREATE INDEX IF NOT EXISTS idx_pmr_v1_book ON {RESULTS_TABLE}(book, updated_at DESC);

CREATE TABLE IF NOT EXISTS {VALIDATION_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    key TEXT NOT NULL,
    value_real REAL,
    value_text TEXT,
    meta_json TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(section, key)
);
CREATE INDEX IF NOT EXISTS idx_pmv_v1_sec ON {VALIDATION_TABLE}(section, key);
"""


def ensure_paper_math_schema(conn: Any) -> None:
    conn.executescript(DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = [
    "BOOK_TABLE",
    "DDL",
    "RESULTS_TABLE",
    "VALIDATION_TABLE",
    "ensure_paper_math_schema",
]
