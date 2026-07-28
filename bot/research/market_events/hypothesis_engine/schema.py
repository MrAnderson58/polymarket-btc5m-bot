"""Research Hypothesis Engine V1 schema — research objects only (no gate/trading)."""

from __future__ import annotations

from typing import Any

STATUS_NEW = "NEW"
STATUS_TESTING = "TESTING"
STATUS_VALIDATED = "VALIDATED"
STATUS_REJECTED = "REJECTED"
STATUS_ARCHIVED = "ARCHIVED"

HYPOTHESIS_STATUSES = (
    STATUS_NEW,
    STATUS_TESTING,
    STATUS_VALIDATED,
    STATUS_REJECTED,
    STATUS_ARCHIVED,
)

TYPE_STRONG_INTERACTION = "Strong Interaction"
TYPE_CONDITIONAL_FEATURE = "Conditional Feature"
TYPE_REPLACEMENT = "Replacement"
TYPE_FEATURE_DEPENDENCY = "Feature Dependency"
TYPE_PATTERN = "Pattern Hypothesis"

HYPOTHESIS_ENGINE_DDL = """
CREATE TABLE IF NOT EXISTS research_hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    generated_from TEXT NOT NULL,
    confidence REAL,
    priority REAL,
    evidence_score INTEGER,
    sample_size INTEGER,
    status TEXT NOT NULL DEFAULT 'NEW',
    created_at INTEGER NOT NULL,
    validated_at INTEGER,
    last_checked_at INTEGER
);

CREATE TABLE IF NOT EXISTS hypothesis_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    reference TEXT,
    weight REAL,
    FOREIGN KEY (hypothesis_id) REFERENCES research_hypotheses(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_research_hypotheses_status
  ON research_hypotheses(status);
CREATE INDEX IF NOT EXISTS idx_research_hypotheses_priority
  ON research_hypotheses(priority DESC);
CREATE INDEX IF NOT EXISTS idx_hypothesis_evidence_hid
  ON hypothesis_evidence(hypothesis_id);
"""


def ensure_hypothesis_engine_schema(conn: Any) -> None:
    try:
        conn.executescript(HYPOTHESIS_ENGINE_DDL)
    except Exception:
        pass
