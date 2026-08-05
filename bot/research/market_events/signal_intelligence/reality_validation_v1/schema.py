"""SQLite schema for Reality Validation Engine V1."""

from __future__ import annotations

from typing import Any

TABLE = "reality_validation_v1"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    key TEXT NOT NULL,
    value_real REAL,
    value_text TEXT,
    meta_json TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(section, key)
);
CREATE INDEX IF NOT EXISTS idx_reality_v1_section ON {TABLE}(section, key);
"""


def ensure_reality_validation_schema(conn: Any) -> None:
    conn.executescript(DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = ["DDL", "TABLE", "ensure_reality_validation_schema"]
