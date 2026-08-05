"""SQLite schema for Elite Candidate Engine V1."""

from __future__ import annotations

from typing import Any

CANDIDATES_TABLE = "elite_candidates_v1"
HISTORY_TABLE = "elite_candidate_history_v1"

ELITE_DDL = f"""
CREATE TABLE IF NOT EXISTS {CANDIDATES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    symbol TEXT,
    opened_at INTEGER,
    direction TEXT,
    category TEXT NOT NULL,
    score REAL NOT NULL,
    base_score REAL NOT NULL,
    learned_score REAL,
    why_json TEXT,
    why_not_json TEXT,
    supporting_modules_json TEXT,
    rejecting_modules_json TEXT,
    historical_wr REAL,
    historical_ev REAL,
    historical_pf REAL,
    historical_similarity REAL,
    current_regime TEXT,
    current_transition TEXT,
    current_fingerprint REAL,
    decision_confidence REAL,
    brain_confidence REAL,
    expected_ev REAL,
    expected_holding_time REAL,
    expected_drawdown REAL,
    components_json TEXT,
    result TEXT,
    pnl REAL,
    updated_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(trade_id)
);
CREATE INDEX IF NOT EXISTS idx_elite_v1_cat ON {CANDIDATES_TABLE}(category, score DESC);
CREATE INDEX IF NOT EXISTS idx_elite_v1_opened ON {CANDIDATES_TABLE}(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_elite_v1_symbol ON {CANDIDATES_TABLE}(symbol, score DESC);

CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    old_score REAL,
    new_score REAL,
    delta REAL,
    pnl REAL,
    result TEXT,
    note TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_elite_hist_v1_trade ON {HISTORY_TABLE}(trade_id, created_at DESC);
"""


def ensure_elite_candidate_schema(conn: Any) -> None:
    conn.executescript(ELITE_DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = [
    "CANDIDATES_TABLE",
    "ELITE_DDL",
    "HISTORY_TABLE",
    "ensure_elite_candidate_schema",
]
