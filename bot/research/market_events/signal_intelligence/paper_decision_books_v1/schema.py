"""DDL for Market Decision Journal + Paper Books V1 (research-only)."""

from __future__ import annotations

from typing import Any

JOURNAL_TABLE = "market_decision_journal_v1"

JOURNAL_DDL = f"""
CREATE TABLE IF NOT EXISTS {JOURNAL_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    symbol TEXT,
    opened_at INTEGER,
    decision TEXT NOT NULL,
    book TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    direction TEXT,
    confidence REAL,
    timeline_similarity REAL,
    fingerprint_similarity REAL,
    dna REAL,
    rules INTEGER,
    edge REAL,
    replay REAL,
    brain REAL,
    causality REAL,
    decision_rank TEXT,
    reasons_json TEXT,
    historical_wr REAL,
    historical_pf REAL,
    historical_ev REAL,
    result TEXT,
    pnl REAL,
    created_at INTEGER NOT NULL,
    UNIQUE(trade_id, book)
);

CREATE INDEX IF NOT EXISTS idx_mdj_v1_book
    ON {JOURNAL_TABLE}(book, accepted, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_mdj_v1_trade
    ON {JOURNAL_TABLE}(trade_id);
CREATE INDEX IF NOT EXISTS idx_mdj_v1_decision
    ON {JOURNAL_TABLE}(decision, book);
"""


def ensure_decision_journal_schema(conn: Any) -> None:
    conn.executescript(JOURNAL_DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = ["JOURNAL_DDL", "JOURNAL_TABLE", "ensure_decision_journal_schema"]
