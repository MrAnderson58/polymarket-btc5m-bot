"""SQLite schema for Replay Recovery Investigation V1 (research-only)."""

from __future__ import annotations

from typing import Any

TABLE = "replay_recovery_v1"

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
CREATE INDEX IF NOT EXISTS idx_rr_v1_section ON {TABLE}(section, key);
"""


def ensure_replay_recovery_schema(conn: Any) -> None:
    conn.executescript(DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = ["DDL", "TABLE", "ensure_replay_recovery_schema"]
