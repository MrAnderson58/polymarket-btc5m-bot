"""SQLite schema for Research Integrity Fix V1."""

from __future__ import annotations

from typing import Any

TABLE = "research_integrity_v1"

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
CREATE INDEX IF NOT EXISTS idx_ri_v1_section ON {TABLE}(section, key);
"""


def ensure_research_integrity_schema(conn: Any) -> None:
    conn.executescript(DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = ["DDL", "TABLE", "ensure_research_integrity_schema"]
