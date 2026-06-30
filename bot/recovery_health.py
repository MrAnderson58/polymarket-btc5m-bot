"""Recovery health metrics for ER SUMMARY monitoring."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from bot.database import count_all_open_positions

RECOVERY_HEALTH_LOOKBACK_SEC = 3600

EVENT_BLOCKED_MAX_OPEN_POSITIONS = "blocked_max_open_positions"
EVENT_RECOVERY_ACTION = "recovery_action"

_ER_TRADE_TABLES_WITH_END_TS: tuple[str, ...] = (
    "early_reversion_trades",
    "early_reversion_v2_trades",
    "early_reversion_v25_trades",
    "early_reversion_v3_trades",
)


@dataclass(frozen=True)
class RecoveryHealthStats:
    open_trades: int
    expired_open_trades: int
    failed_exit_intents: int
    blocked_max_open_positions_1h: int
    recovery_actions_1h: int


def record_health_event(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    event_ts: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO er_health_events (event_type, event_ts)
        VALUES (?, ?)
        """,
        (event_type, event_ts if event_ts is not None else int(time.time())),
    )


def _count_health_events(
    conn: sqlite3.Connection,
    event_type: str,
    since_ts: int,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM er_health_events
        WHERE event_type = ? AND event_ts >= ?
        """,
        (event_type, since_ts),
    ).fetchone()
    return int(row["count"]) if row is not None else 0


def _count_expired_open_trades(conn: sqlite3.Connection, now_ts: int) -> int:
    total = 0
    for table in _ER_TRADE_TABLES_WITH_END_TS:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM {table}
            WHERE status = 'open' AND end_ts < ?
            """,
            (now_ts,),
        ).fetchone()
        total += int(row["count"]) if row is not None else 0
    return total


def _count_failed_exit_intents(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM order_intents
        WHERE idempotency_key LIKE '%:exit' AND status = 'failed'
        """
    ).fetchone()
    return int(row["count"]) if row is not None else 0


def _count_recovery_actions_1h(conn: sqlite3.Connection, since_ts: int) -> int:
    from_events = _count_health_events(conn, EVENT_RECOVERY_ACTION, since_ts)
    from_trades = 0
    for table in _ER_TRADE_TABLES_WITH_END_TS[1:]:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM {table}
            WHERE exit_reason = 'RECOVERY_NO_POSITION'
              AND closed_at IS NOT NULL
              AND CAST(strftime('%s', closed_at) AS INTEGER) >= ?
            """,
            (since_ts,),
        ).fetchone()
        from_trades += int(row["count"]) if row is not None else 0
    return max(from_events, from_trades)


def fetch_recovery_health_stats(
    conn: sqlite3.Connection,
    *,
    now_ts: int | None = None,
    lookback_sec: int = RECOVERY_HEALTH_LOOKBACK_SEC,
) -> RecoveryHealthStats:
    if now_ts is None:
        now_ts = int(time.time())
    since_ts = now_ts - lookback_sec
    return RecoveryHealthStats(
        open_trades=count_all_open_positions(conn),
        expired_open_trades=_count_expired_open_trades(conn, now_ts),
        failed_exit_intents=_count_failed_exit_intents(conn),
        blocked_max_open_positions_1h=_count_health_events(
            conn,
            EVENT_BLOCKED_MAX_OPEN_POSITIONS,
            since_ts,
        ),
        recovery_actions_1h=_count_recovery_actions_1h(conn, since_ts),
    )


def format_recovery_health_block(stats: RecoveryHealthStats) -> str:
    return "\n".join(
        [
            "RECOVERY HEALTH",
            "",
            f"Open trades: {stats.open_trades}",
            f"Expired open trades: {stats.expired_open_trades}",
            f"Failed exit intents: {stats.failed_exit_intents}",
            (
                "Blocked by MAX_OPEN_POSITIONS (1h): "
                f"{stats.blocked_max_open_positions_1h}"
            ),
            f"Recovery actions (1h): {stats.recovery_actions_1h}",
        ]
    )
