"""Phase F.5 — TOP-N Telegram priority when many shocks fire together."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import F5_TOP_N_TELEGRAM

PRIORITY_WINDOW_SEC = 300


def _window_bucket(now: int | None = None) -> int:
    ts = now or int(time.time())
    return ts // PRIORITY_WINDOW_SEC


def register_priority(
    conn: Any,
    *,
    event_id: int,
    priority_score: float,
    dynamic_confidence: float,
) -> tuple[float, int]:
    bucket = _window_bucket()
    now = int(time.time())

    existing = conn.execute(
        "SELECT id FROM market_events_signal_priority_f5 WHERE event_id = ? AND window_bucket = ?",
        (event_id, bucket),
    ).fetchone()
    if not existing:
        insert_returning_id(
            conn,
            """
            INSERT INTO market_events_signal_priority_f5 (
              event_id, dynamic_confidence, priority_score, rank_position,
              window_bucket, telegram_sent, created_at
            ) VALUES (?, ?, ?, 0, ?, 0, ?)
            """,
            (event_id, dynamic_confidence, priority_score, bucket, now),
        )
    else:
        conn.execute(
            """
            UPDATE market_events_signal_priority_f5
            SET dynamic_confidence = ?, priority_score = ?
            WHERE event_id = ? AND window_bucket = ?
            """,
            (dynamic_confidence, priority_score, event_id, bucket),
        )

    rows = conn.execute(
        """
        SELECT event_id, priority_score FROM market_events_signal_priority_f5
        WHERE window_bucket = ?
        ORDER BY priority_score DESC, dynamic_confidence DESC, event_id ASC
        """,
        (bucket,),
    ).fetchall()

    position = 1
    for i, r in enumerate(rows, start=1):
        conn.execute(
            """
            UPDATE market_events_signal_priority_f5 SET rank_position = ?
            WHERE event_id = ? AND window_bucket = ?
            """,
            (i, int(r["event_id"]), bucket),
        )
        if int(r["event_id"]) == event_id:
            position = i

    return priority_score, position


def count_telegram_sent_in_window(conn: Any, *, bucket: int | None = None) -> int:
    b = bucket if bucket is not None else _window_bucket()
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_priority_f5
        WHERE window_bucket = ? AND telegram_sent = 1
        """,
        (b,),
    ).fetchone()
    return int(row["n"] if row else 0)


def should_send_f5_telegram(
    conn: Any,
    *,
    event_id: int,
    dynamic_confidence: float,
    priority_score: float,
    min_confidence: float,
) -> tuple[bool, str]:
    if dynamic_confidence < min_confidence:
        return False, "low_confidence"

    _, position = register_priority(
        conn,
        event_id=event_id,
        priority_score=priority_score,
        dynamic_confidence=dynamic_confidence,
    )
    if position > F5_TOP_N_TELEGRAM:
        return False, "not_top3"

    bucket = _window_bucket()
    if count_telegram_sent_in_window(conn, bucket=bucket) >= F5_TOP_N_TELEGRAM:
        return False, "window_full"

    row = conn.execute(
        """
        SELECT telegram_sent FROM market_events_signal_priority_f5
        WHERE event_id = ? AND window_bucket = ?
        """,
        (event_id, bucket),
    ).fetchone()
    if row and int(row["telegram_sent"]):
        return False, "already_sent"

    return True, "ok"


def mark_f5_telegram_sent(conn: Any, event_id: int, *, skipped_reason: str | None = None) -> None:
    bucket = _window_bucket()
    if skipped_reason:
        conn.execute(
            """
            UPDATE market_events_signal_priority_f5
            SET telegram_skipped_reason = ?
            WHERE event_id = ? AND window_bucket = ?
            """,
            (skipped_reason, event_id, bucket),
        )
        return
    conn.execute(
        """
        UPDATE market_events_signal_priority_f5 SET telegram_sent = 1
        WHERE event_id = ? AND window_bucket = ?
        """,
        (event_id, bucket),
    )


def dashboard_skipped_count(conn: Any, *, since_ts: int | None = None) -> int:
    sql = (
        "SELECT COUNT(*) AS n FROM market_events_signal_priority_f5 "
        "WHERE telegram_sent = 0 AND telegram_skipped_reason IS NOT NULL"
    )
    params: tuple[Any, ...] = ()
    if since_ts is not None:
        sql += " AND created_at >= ?"
        params = (since_ts,)
    row = conn.execute(sql, params).fetchone()
    return int(row["n"] if row else 0)
