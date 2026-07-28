"""Experiment Engine V1 schema — historical hypothesis checks (research only)."""

from __future__ import annotations

from typing import Any

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_VALIDATED = "VALIDATED"
STATUS_REJECTED = "REJECTED"
STATUS_FAILED = "FAILED"
STATUS_WEAK = "WEAK"

EXPERIMENT_STATUSES = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_VALIDATED,
    STATUS_REJECTED,
    STATUS_FAILED,
    STATUS_WEAK,
)

TYPE_SINGLE_FILTER = "Single Filter"
TYPE_INTERACTION = "Interaction"
TYPE_REPLACEMENT = "Replacement"
TYPE_DEPENDENCY = "Dependency"
TYPE_PATTERN = "Pattern"

EXPERIMENT_TYPES = (
    TYPE_SINGLE_FILTER,
    TYPE_INTERACTION,
    TYPE_REPLACEMENT,
    TYPE_DEPENDENCY,
    TYPE_PATTERN,
)

EXPERIMENT_ENGINE_DDL = """
CREATE TABLE IF NOT EXISTS research_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id INTEGER NOT NULL,
    experiment_type TEXT NOT NULL,
    dataset_size INTEGER,
    ev_before REAL,
    ev_after REAL,
    pf_before REAL,
    pf_after REAL,
    wr_before REAL,
    wr_after REAL,
    delta_ev REAL,
    delta_pf REAL,
    delta_wr REAL,
    p_value REAL,
    confidence_interval TEXT,
    effect_size REAL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at INTEGER NOT NULL,
    finished_at INTEGER,
    mfe_after REAL,
    mae_after REAL,
    notes TEXT,
    FOREIGN KEY (hypothesis_id) REFERENCES research_hypotheses(id)
);

CREATE TABLE IF NOT EXISTS experiment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL,
    run_time INTEGER NOT NULL,
    dataset_hash TEXT,
    duration_ms INTEGER,
    success INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    FOREIGN KEY (experiment_id) REFERENCES research_experiments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_research_experiments_status
  ON research_experiments(status);
CREATE INDEX IF NOT EXISTS idx_research_experiments_hid
  ON research_experiments(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_research_experiments_effect
  ON research_experiments(effect_size DESC);
CREATE INDEX IF NOT EXISTS idx_experiment_runs_eid
  ON experiment_runs(experiment_id, run_time DESC);
"""


def ensure_experiment_engine_schema(conn: Any) -> None:
    try:
        conn.executescript(EXPERIMENT_ENGINE_DDL)
    except Exception:
        pass
