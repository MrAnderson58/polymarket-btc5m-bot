"""Phase F.3 Trend Shock — signal ranking and alert gating."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.db import insert_returning_id

RANKING_WINDOW_SEC = 300
MAX_ALERTS_PER_WINDOW = 3
MIN_RANK_SCORE = 45.0


def compute_rank_score(conn: Any, event_id: int) -> float:
    trend = conn.execute(
        "SELECT trend_score FROM market_events_trend_shock WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    trend_score = float(trend["trend_score"]) if trend else 0.0

    f2 = conn.execute(
        "SELECT confidence_score, reversal_probability FROM market_events_signal_reports_f2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if f2:
        conf = float(f2["confidence_score"] or 0)
        rev = float(f2["reversal_probability"] or 0.5)
        if rev <= 1.0:
            rev *= 100.0
        return round(trend_score * 0.35 + conf * 8 + rev * 0.25, 2)

    row = conn.execute(
        "SELECT ABS(return_pct) AS ret FROM market_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    ret = float(row["ret"]) if row else 0.0
    return round(trend_score * 0.5 + ret * 5, 2)


def _window_bucket(now: int | None = None) -> int:
    ts = now or int(time.time())
    return ts // RANKING_WINDOW_SEC


def register_ranking(conn: Any, event_id: int) -> tuple[float, int]:
    score = compute_rank_score(conn, event_id)
    bucket = _window_bucket()
    now = int(time.time())

    peers = conn.execute(
        """
        SELECT event_id, rank_score FROM market_events_alert_rankings_f3
        WHERE window_bucket = ?
        ORDER BY rank_score DESC
        """,
        (bucket,),
    ).fetchall()

    existing = conn.execute(
        "SELECT id FROM market_events_alert_rankings_f3 WHERE event_id = ? AND window_bucket = ?",
        (event_id, bucket),
    ).fetchone()

    if not existing:
        insert_returning_id(
            conn,
            """
            INSERT INTO market_events_alert_rankings_f3 (
              event_id, rank_score, rank_position, window_bucket, alerted, created_at
            ) VALUES (?, ?, 0, ?, 0, ?)
            """,
            (event_id, score, bucket, now),
        )

    conn.execute(
        """
        UPDATE market_events_alert_rankings_f3 SET rank_score = ?
        WHERE event_id = ? AND window_bucket = ?
        """,
        (score, event_id, bucket),
    )

    all_rows = conn.execute(
        """
        SELECT event_id, rank_score FROM market_events_alert_rankings_f3
        WHERE window_bucket = ?
        ORDER BY rank_score DESC, event_id ASC
        """,
        (bucket,),
    ).fetchall()

    position = 1
    for i, r in enumerate(all_rows, start=1):
        conn.execute(
            """
            UPDATE market_events_alert_rankings_f3 SET rank_position = ?
            WHERE event_id = ? AND window_bucket = ?
            """,
            (i, int(r["event_id"]), bucket),
        )
        if int(r["event_id"]) == event_id:
            position = i

    return score, position


def should_send_ranked_alert(conn: Any, event_id: int) -> bool:
    score, position = register_ranking(conn, event_id)
    if score < MIN_RANK_SCORE:
        return False
    if position > MAX_ALERTS_PER_WINDOW:
        return False

    alerted = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_alert_rankings_f3
        WHERE window_bucket = ? AND alerted = 1
        """,
        (_window_bucket(),),
    ).fetchone()
    if int(alerted["n"] if alerted else 0) >= MAX_ALERTS_PER_WINDOW:
        return False

    row = conn.execute(
        "SELECT alerted FROM market_events_alert_rankings_f3 WHERE event_id = ? AND window_bucket = ?",
        (event_id, _window_bucket()),
    ).fetchone()
    if row and row["alerted"]:
        return False

    return True


def mark_alert_sent(conn: Any, event_id: int) -> None:
    conn.execute(
        """
        UPDATE market_events_alert_rankings_f3 SET alerted = 1
        WHERE event_id = ? AND window_bucket = ?
        """,
        (event_id, _window_bucket()),
    )
