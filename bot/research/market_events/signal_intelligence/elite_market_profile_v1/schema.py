"""SQLite schema for Elite Market Profile V1."""

from __future__ import annotations

from typing import Any

PROFILE_TABLE = "elite_market_profile_v1"
COMBOS_TABLE = "elite_profile_combos_v1"
COMPARE_TABLE = "elite_vs_ignore_v1"

PROFILE_DDL = f"""
CREATE TABLE IF NOT EXISTS {PROFILE_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension TEXT NOT NULL,
    key TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    pct REAL,
    wr REAL,
    pf REAL,
    ev REAL,
    meta_json TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(dimension, key)
);
CREATE INDEX IF NOT EXISTS idx_emp_v1_dim ON {PROFILE_TABLE}(dimension, n DESC);

CREATE TABLE IF NOT EXISTS {COMBOS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_key TEXT NOT NULL UNIQUE,
    pattern TEXT NOT NULL,
    tags_json TEXT,
    n INTEGER NOT NULL DEFAULT 0,
    wr REAL,
    pf REAL,
    ev REAL,
    rank INTEGER,
    meta_json TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_epc_v1_n ON {COMBOS_TABLE}(n DESC);

CREATE TABLE IF NOT EXISTS {COMPARE_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature TEXT NOT NULL UNIQUE,
    elite_pct REAL,
    ignore_pct REAL,
    delta_pct REAL,
    elite_n INTEGER,
    ignore_n INTEGER,
    meta_json TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evi_v1_delta ON {COMPARE_TABLE}(delta_pct DESC);
"""


def ensure_elite_market_profile_schema(conn: Any) -> None:
    conn.executescript(PROFILE_DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = [
    "COMBOS_TABLE",
    "COMPARE_TABLE",
    "PROFILE_DDL",
    "PROFILE_TABLE",
    "ensure_elite_market_profile_schema",
]
