"""Experiment queue — scientist_experiments table."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def persist_hypothesis(conn: sqlite3.Connection, hypothesis: dict[str, Any]) -> int:
    conn.execute(
        """
        INSERT INTO scientist_hypotheses (
            fingerprint, description, source, sample_n, confidence,
            expected_improvement, expected_pf, expected_wr,
            hypothesis_type, params_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            description = excluded.description,
            sample_n = excluded.sample_n,
            confidence = excluded.confidence,
            expected_improvement = excluded.expected_improvement,
            expected_pf = excluded.expected_pf,
            expected_wr = excluded.expected_wr
        """,
        (
            hypothesis["fingerprint"],
            hypothesis["description"],
            hypothesis["source"],
            hypothesis["sample_n"],
            hypothesis["confidence"],
            hypothesis.get("expected_improvement"),
            hypothesis.get("expected_pf"),
            hypothesis.get("expected_wr"),
            hypothesis["hypothesis_type"],
            json.dumps(hypothesis.get("params", {}), ensure_ascii=False),
            hypothesis.get("created_at", datetime.now(timezone.utc).isoformat()),
        ),
    )
    row = conn.execute(
        "SELECT id FROM scientist_hypotheses WHERE fingerprint = ?",
        (hypothesis["fingerprint"],),
    ).fetchone()
    return int(row["id"])


def upsert_experiment(
    conn: sqlite3.Connection,
    *,
    hypothesis_id: int,
    hypothesis: dict[str, Any],
    validation: dict[str, Any],
    ranking: dict[str, Any],
) -> int:
    title = hypothesis["hypothesis_type"].replace("_", " ").title()
    status = validation["status"]
    if status == "PASSED" and not validation.get("eligible_for_recommendation"):
        status = "REJECTED"

    conn.execute(
        """
        INSERT INTO scientist_experiments (
            hypothesis_id, title, description, status, priority,
            confidence, expected_pf, expected_wr, risk_level,
            validation_json, ranking_score, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(hypothesis_id) DO UPDATE SET
            description = excluded.description,
            status = excluded.status,
            priority = excluded.priority,
            confidence = excluded.confidence,
            expected_pf = excluded.expected_pf,
            expected_wr = excluded.expected_wr,
            risk_level = excluded.risk_level,
            validation_json = excluded.validation_json,
            ranking_score = excluded.ranking_score,
            updated_at = datetime('now')
        """,
        (
            hypothesis_id,
            title,
            hypothesis["description"],
            status,
            ranking["priority"],
            hypothesis["confidence"],
            hypothesis.get("expected_pf"),
            hypothesis.get("expected_wr"),
            ranking["risk"],
            json.dumps(validation, ensure_ascii=False),
            ranking["score"],
        ),
    )
    row = conn.execute(
        "SELECT id FROM scientist_experiments WHERE hypothesis_id = ?",
        (hypothesis_id,),
    ).fetchone()
    return int(row["id"])


def load_experiments(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            """
            SELECT e.*, h.hypothesis_type, h.fingerprint, h.source
            FROM scientist_experiments e
            JOIN scientist_hypotheses h ON h.id = e.hypothesis_id
            WHERE e.status = ?
            ORDER BY e.ranking_score DESC, e.updated_at DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT e.*, h.hypothesis_type, h.fingerprint, h.source
            FROM scientist_experiments e
            JOIN scientist_hypotheses h ON h.id = e.hypothesis_id
            ORDER BY e.ranking_score DESC, e.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["validation"] = json.loads(d.pop("validation_json") or "{}")
        out.append(d)
    return out


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM scientist_experiments
        GROUP BY status
        """
    ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}
