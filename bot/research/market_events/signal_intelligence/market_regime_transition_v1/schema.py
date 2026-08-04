"""SQLite schema for Market Regime Transition Engine V1."""

from __future__ import annotations

from typing import Any

TRANSITIONS_TABLE = "market_regime_transitions_v1"
EDGES_TABLE = "transition_edges_v1"
SEQUENCES_TABLE = "transition_sequences_v1"

TRANSITION_DDL = f"""
CREATE TABLE IF NOT EXISTS {TRANSITIONS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_key TEXT NOT NULL UNIQUE,
    lookback INTEGER NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    pattern TEXT,
    n INTEGER NOT NULL DEFAULT 0,
    wr REAL,
    pf REAL,
    ev REAL,
    sharpe REAL,
    max_dd REAL,
    ci_lo REAL,
    ci_hi REAL,
    perm_p REAL,
    oos_wr REAL,
    oos_ev REAL,
    temporal_stability REAL,
    ready INTEGER NOT NULL DEFAULT 0,
    score REAL,
    kind TEXT,
    meta_json TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mrt_v1_ready ON {TRANSITIONS_TABLE}(ready, score DESC);
CREATE INDEX IF NOT EXISTS idx_mrt_v1_kind ON {TRANSITIONS_TABLE}(kind, n DESC);

CREATE TABLE IF NOT EXISTS {EDGES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    prob REAL,
    expected_duration REAL,
    expected_ev REAL,
    expected_wr REAL,
    expected_pf REAL,
    UNIQUE(from_state, to_state)
);

CREATE TABLE IF NOT EXISTS {SEQUENCES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_key TEXT NOT NULL UNIQUE,
    chain TEXT NOT NULL,
    depth INTEGER NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    wr REAL,
    pf REAL,
    ev REAL,
    sharpe REAL,
    ready INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mts_v1_n ON {SEQUENCES_TABLE}(n DESC);
"""


def ensure_regime_transition_schema(conn: Any) -> None:
    conn.executescript(TRANSITION_DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = [
    "EDGES_TABLE",
    "SEQUENCES_TABLE",
    "TRANSITION_DDL",
    "TRANSITIONS_TABLE",
    "ensure_regime_transition_schema",
]
