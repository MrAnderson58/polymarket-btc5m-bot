"""Runtime observability for evolution observe hooks."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ensure_observe_stats_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evolution_observe_stats (
            hook TEXT PRIMARY KEY,
            last_run_at TEXT,
            last_success_at TEXT,
            last_error_at TEXT,
            last_error TEXT,
            error_count INTEGER NOT NULL DEFAULT 0,
            total_parameter_evals INTEGER NOT NULL DEFAULT 0,
            total_regime_evals INTEGER NOT NULL DEFAULT 0,
            last_parameter_eval_at TEXT,
            last_regime_eval_at TEXT
        )
        """
    )


def record_observe_run(
    conn: sqlite3.Connection,
    *,
    hook: str,
    parameter_evals: int = 0,
    regime_evals: int = 0,
    error: str | None = None,
) -> None:
    ensure_observe_stats_table(conn)
    now = _utc_now()
    row = conn.execute(
        "SELECT * FROM evolution_observe_stats WHERE hook = ?",
        (hook,),
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO evolution_observe_stats (
                hook, last_run_at, last_success_at, last_error_at, last_error,
                error_count, total_parameter_evals, total_regime_evals,
                last_parameter_eval_at, last_regime_eval_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hook,
                now,
                now if error is None else None,
                now if error else None,
                error,
                1 if error else 0,
                parameter_evals,
                regime_evals,
                now if parameter_evals else None,
                now if regime_evals else None,
            ),
        )
        return

    conn.execute(
        """
        UPDATE evolution_observe_stats
        SET last_run_at = ?,
            last_success_at = CASE WHEN ? IS NULL THEN ? ELSE last_success_at END,
            last_error_at = CASE WHEN ? IS NOT NULL THEN ? ELSE last_error_at END,
            last_error = COALESCE(?, last_error),
            error_count = error_count + ?,
            total_parameter_evals = total_parameter_evals + ?,
            total_regime_evals = total_regime_evals + ?,
            last_parameter_eval_at = CASE WHEN ? > 0 THEN ? ELSE last_parameter_eval_at END,
            last_regime_eval_at = CASE WHEN ? > 0 THEN ? ELSE last_regime_eval_at END
        WHERE hook = ?
        """,
        (
            now,
            error,
            now,
            error,
            now,
            error,
            1 if error else 0,
            parameter_evals,
            regime_evals,
            parameter_evals,
            now,
            regime_evals,
            now,
            hook,
        ),
    )


def load_observe_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_observe_stats_table(conn)
    rows = conn.execute("SELECT * FROM evolution_observe_stats").fetchall()
    return {str(r["hook"]): dict(r) for r in rows}
