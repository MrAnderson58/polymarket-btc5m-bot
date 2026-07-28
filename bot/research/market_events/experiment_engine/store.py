"""Persist experiments and runs."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.experiment_engine.schema import (
    STATUS_PENDING,
    ensure_experiment_engine_schema,
)


def _now() -> int:
    return int(time.time())


def create_experiment(
    conn: Any,
    *,
    hypothesis_id: int,
    experiment_type: str,
    now: int | None = None,
) -> int:
    ensure_experiment_engine_schema(conn)
    ts = int(now if now is not None else _now())
    cur = conn.execute(
        """
        INSERT INTO research_experiments (
          hypothesis_id, experiment_type, status, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (int(hypothesis_id), experiment_type, STATUS_PENDING, ts),
    )
    return int(cur.lastrowid)


def update_experiment_result(
    conn: Any,
    experiment_id: int,
    *,
    dataset_size: int | None,
    ev_before: float | None,
    ev_after: float | None,
    pf_before: float | None,
    pf_after: float | None,
    wr_before: float | None,
    wr_after: float | None,
    delta_ev: float | None,
    delta_pf: float | None,
    delta_wr: float | None,
    p_value: float | None,
    confidence_interval: str | None,
    effect_size: float | None,
    status: str,
    mfe_after: float | None = None,
    mae_after: float | None = None,
    notes: str | None = None,
    finished_at: int | None = None,
) -> None:
    ts = int(finished_at if finished_at is not None else _now())
    conn.execute(
        """
        UPDATE research_experiments SET
          dataset_size=?, ev_before=?, ev_after=?, pf_before=?, pf_after=?,
          wr_before=?, wr_after=?, delta_ev=?, delta_pf=?, delta_wr=?,
          p_value=?, confidence_interval=?, effect_size=?, status=?,
          finished_at=?, mfe_after=?, mae_after=?, notes=?
        WHERE id=?
        """,
        (
            dataset_size,
            ev_before,
            ev_after,
            pf_before,
            pf_after,
            wr_before,
            wr_after,
            delta_ev,
            delta_pf,
            delta_wr,
            p_value,
            confidence_interval,
            effect_size,
            status,
            ts,
            mfe_after,
            mae_after,
            notes,
            int(experiment_id),
        ),
    )


def add_experiment_run(
    conn: Any,
    *,
    experiment_id: int,
    dataset_hash: str | None,
    duration_ms: int | None,
    success: bool,
    notes: str | None = None,
    run_time: int | None = None,
) -> int:
    ts = int(run_time if run_time is not None else _now())
    cur = conn.execute(
        """
        INSERT INTO experiment_runs (
          experiment_id, run_time, dataset_hash, duration_ms, success, notes
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(experiment_id),
            ts,
            dataset_hash,
            duration_ms,
            1 if success else 0,
            notes,
        ),
    )
    return int(cur.lastrowid)


def list_experiments(conn: Any) -> list[dict[str, Any]]:
    ensure_experiment_engine_schema(conn)
    try:
        rows = conn.execute(
            """
            SELECT e.*, h.title AS hypothesis_title, h.hypothesis_key,
                   h.status AS hypothesis_status, h.generated_from
            FROM research_experiments e
            LEFT JOIN research_hypotheses h ON h.id = e.hypothesis_id
            ORDER BY e.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def list_recent_runs(conn: Any, *, limit: int = 30) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT r.*, e.experiment_type, e.hypothesis_id, e.status AS experiment_status
            FROM experiment_runs r
            JOIN research_experiments e ON e.id = r.experiment_id
            ORDER BY r.run_time DESC, r.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
