"""SQLite schema for Math Decision Funnel V1 (research-only)."""

from __future__ import annotations

from typing import Any

FUNNEL_TABLE = "decision_funnel_v1"
REJECTIONS_TABLE = "decision_rejections_v1"

DDL = f"""
CREATE TABLE IF NOT EXISTS {FUNNEL_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    input_n INTEGER NOT NULL DEFAULT 0,
    accepted_n INTEGER NOT NULL DEFAULT 0,
    rejected_n INTEGER NOT NULL DEFAULT 0,
    acceptance_pct REAL,
    wr REAL,
    pf REAL,
    ev REAL,
    sharpe REAL,
    meta_json TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(stage)
);
CREATE INDEX IF NOT EXISTS idx_df_v1_order ON {FUNNEL_TABLE}(stage_order);

CREATE TABLE IF NOT EXISTS {REJECTIONS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    symbol TEXT,
    opened_at INTEGER,
    first_rejector TEXT NOT NULL,
    all_rejectors_json TEXT,
    confidence REAL,
    historical_wr REAL,
    historical_ev REAL,
    pnl REAL,
    recoverable INTEGER NOT NULL DEFAULT 0,
    lost_ev REAL,
    meta_json TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(trade_id)
);
CREATE INDEX IF NOT EXISTS idx_dr_v1_rejector ON {REJECTIONS_TABLE}(first_rejector);
CREATE INDEX IF NOT EXISTS idx_dr_v1_recoverable ON {REJECTIONS_TABLE}(recoverable);
"""


def ensure_decision_funnel_schema(conn: Any) -> None:
    conn.executescript(DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = [
    "DDL",
    "FUNNEL_TABLE",
    "REJECTIONS_TABLE",
    "ensure_decision_funnel_schema",
]
