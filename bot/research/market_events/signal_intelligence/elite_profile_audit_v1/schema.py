"""SQLite schema for Elite Profile Audit V1."""

from __future__ import annotations

from typing import Any

AUDIT_TABLE = "elite_profile_audit_v1"
BIAS_TABLE = "elite_bias_report_v1"

AUDIT_DDL = f"""
CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT,
    n INTEGER,
    pct REAL,
    p_value REAL,
    lift REAL,
    meta_json TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(section, key)
);
CREATE INDEX IF NOT EXISTS idx_epa_v1_section ON {AUDIT_TABLE}(section, n DESC);

CREATE TABLE IF NOT EXISTS {BIAS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bias_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    score REAL,
    evidence TEXT,
    largest INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ebr_v1_sev ON {BIAS_TABLE}(severity, score DESC);
"""


def ensure_elite_profile_audit_schema(conn: Any) -> None:
    conn.executescript(AUDIT_DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = [
    "AUDIT_DDL",
    "AUDIT_TABLE",
    "BIAS_TABLE",
    "ensure_elite_profile_audit_schema",
]
