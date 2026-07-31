"""Alpha Validation Engine V2 schema — research only (no gate/trading)."""

from __future__ import annotations

from typing import Any

STATUS_PENDING = "PENDING"
STATUS_PASSED = "PASSED"
STATUS_REJECTED = "REJECTED"
STATUS_INSUFFICIENT = "INSUFFICIENT"

VALIDATION_STATUSES = (
    STATUS_PENDING,
    STATUS_PASSED,
    STATUS_REJECTED,
    STATUS_INSUFFICIENT,
)

ALPHA_VALIDATION_DDL = """
CREATE TABLE IF NOT EXISTS alpha_validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_label TEXT NOT NULL,
    features_json TEXT,
    status TEXT NOT NULL,
    reject_reason TEXT,
    n_total INTEGER,
    n_matched INTEGER,
    expectancy REAL,
    pf REAL,
    winrate REAL,
    sharpe REAL,
    ci_lo REAL,
    ci_hi REAL,
    p_value REAL,
    walk_forward_json TEXT,
    rolling_json TEXT,
    oos_json TEXT,
    monte_carlo_json TEXT,
    stability_json TEXT,
    metrics_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(run_id, rule_id)
);

CREATE TABLE IF NOT EXISTS alpha_validation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    n_candidates INTEGER,
    n_passed INTEGER,
    n_rejected INTEGER,
    n_insufficient INTEGER,
    n_rows INTEGER,
    summary_json TEXT,
    report_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_alpha_validations_run
  ON alpha_validations(run_id);
CREATE INDEX IF NOT EXISTS idx_alpha_validations_status
  ON alpha_validations(status);
CREATE INDEX IF NOT EXISTS idx_alpha_validations_rule
  ON alpha_validations(rule_id);
CREATE INDEX IF NOT EXISTS idx_alpha_validation_history_run
  ON alpha_validation_history(run_id);
CREATE INDEX IF NOT EXISTS idx_alpha_validation_history_started
  ON alpha_validation_history(started_at DESC);
"""


def ensure_alpha_validation_schema(conn: Any) -> None:
    try:
        conn.executescript(ALPHA_VALIDATION_DDL)
    except Exception:
        pass


__all__ = [
    "ALPHA_VALIDATION_DDL",
    "STATUS_INSUFFICIENT",
    "STATUS_PASSED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "VALIDATION_STATUSES",
    "ensure_alpha_validation_schema",
]
