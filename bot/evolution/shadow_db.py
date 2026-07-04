"""SQLite persistence for evolution shadow experiments."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from bot.evolution.constants import SHADOW_TARGET_SAMPLE


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_running_shadow(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM evolution_shadow
        WHERE status = 'RUNNING'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return _row_to_dict(row)


def get_latest_shadow(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM evolution_shadow
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return _row_to_dict(row)


def get_shadow_by_id(conn: sqlite3.Connection, shadow_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM evolution_shadow WHERE id = ?",
        (shadow_id,),
    ).fetchone()
    return _row_to_dict(row)


def create_shadow_experiment(
    conn: sqlite3.Connection,
    *,
    parameter: str,
    current_value: float,
    shadow_value: float,
    target_sample_size: int = SHADOW_TARGET_SAMPLE,
) -> dict[str, Any]:
    existing = get_running_shadow(conn)
    if existing is not None:
        return existing

    created_at = _utc_now()
    cur = conn.execute(
        """
        INSERT INTO evolution_shadow (
            parameter, current_value, shadow_value, status,
            created_at, sample_size, target_sample_size
        ) VALUES (?, ?, ?, 'RUNNING', ?, 0, ?)
        """,
        (parameter, current_value, shadow_value, created_at, target_sample_size),
    )
    shadow_id = int(cur.lastrowid)
    row = get_shadow_by_id(conn, shadow_id)
    assert row is not None
    return row


def insert_shadow_evaluation(
    conn: sqlite3.Connection,
    *,
    shadow_id: int,
    trade_id: int,
    market_slug: str,
    entry_price: float,
    entry_ts: int,
    shadow_decision: str,
    live_pnl: float,
    shadow_pnl: float,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO evolution_shadow_evaluations (
            shadow_id, trade_id, market_slug, entry_price, entry_ts,
            shadow_decision, live_pnl, shadow_pnl, evaluated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shadow_id,
            trade_id,
            market_slug,
            entry_price,
            entry_ts,
            shadow_decision,
            live_pnl,
            shadow_pnl,
            _utc_now(),
        ),
    )
    sample_size = conn.execute(
        "SELECT COUNT(*) AS n FROM evolution_shadow_evaluations WHERE shadow_id = ?",
        (shadow_id,),
    ).fetchone()["n"]
    conn.execute(
        "UPDATE evolution_shadow SET sample_size = ? WHERE id = ?",
        (int(sample_size), shadow_id),
    )


def fetch_unevaluated_trades(
    conn: sqlite3.Connection,
    *,
    shadow_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.*
        FROM early_reversion_v2_trades t
        LEFT JOIN evolution_shadow_evaluations e
          ON e.shadow_id = ? AND e.trade_id = t.id
        JOIN evolution_shadow s ON s.id = ?
        WHERE t.status = 'closed'
          AND t.closed_at >= s.created_at
          AND e.id IS NULL
        ORDER BY t.entry_ts ASC
        """,
        (shadow_id, shadow_id),
    ).fetchall()


def load_shadow_evaluations(
    conn: sqlite3.Connection,
    shadow_id: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM evolution_shadow_evaluations
        WHERE shadow_id = ?
        ORDER BY entry_ts ASC
        """,
        (shadow_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def complete_shadow_experiment(
    conn: sqlite3.Connection,
    *,
    shadow_id: int,
    shadow_pf: float,
    live_pf: float,
    shadow_wr: float,
    live_wr: float,
    shadow_dd: float,
    live_dd: float,
    verdict: str,
) -> None:
    conn.execute(
        """
        UPDATE evolution_shadow
        SET status = 'COMPLETE',
            completed_at = ?,
            shadow_pf = ?,
            live_pf = ?,
            shadow_wr = ?,
            live_wr = ?,
            shadow_dd = ?,
            live_dd = ?,
            verdict = ?
        WHERE id = ?
        """,
        (
            _utc_now(),
            shadow_pf,
            live_pf,
            shadow_wr,
            live_wr,
            shadow_dd,
            live_dd,
            verdict,
            shadow_id,
        ),
    )
