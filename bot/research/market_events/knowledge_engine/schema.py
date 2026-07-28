"""Knowledge Engine V1 schema — validated analytics memory (not trade signals)."""

from __future__ import annotations

from typing import Any

KNOWLEDGE_ENGINE_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_name TEXT NOT NULL UNIQUE,
    feature_key TEXT,
    ev_delta REAL,
    pf_delta REAL,
    wr_delta REAL,
    stability_score REAL,
    sample_size INTEGER,
    confidence REAL,
    verdict TEXT,
    rule_text TEXT,
    last_updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    feature_name TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    ev_delta REAL,
    pf_delta REAL,
    wr_delta REAL,
    sample_size INTEGER,
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'candidate',
    last_updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key TEXT NOT NULL UNIQUE,
    feature_a TEXT NOT NULL,
    feature_b TEXT NOT NULL,
    synergy REAL,
    ev_joint REAL,
    ev_a REAL,
    ev_b REAL,
    sample_size INTEGER,
    stronger_together INTEGER NOT NULL DEFAULT 0,
    last_updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    note TEXT,
    recorded_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_features_verdict ON knowledge_features(verdict);
CREATE INDEX IF NOT EXISTS idx_knowledge_rules_status ON knowledge_rules(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_history_entity ON knowledge_history(entity_type, entity_key, recorded_at DESC);
"""


def ensure_knowledge_engine_schema(conn: Any) -> None:
    try:
        conn.executescript(KNOWLEDGE_ENGINE_DDL)
    except Exception:
        pass
