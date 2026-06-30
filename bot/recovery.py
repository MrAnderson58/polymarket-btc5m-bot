"""Startup recovery for open paper trades persisted in SQLite."""

from __future__ import annotations

import logging
import sqlite3
import time

from bot.database import connect, init_db
from bot.execution import reconcile_unrecorded_entry_intents
from bot.exit_recovery import reconcile_stuck_exits
from bot.early_reversion import close_due_early_reversion_trades
from bot.early_reversion_v2 import close_due_early_reversion_v2_trades
from bot.early_reversion_v25 import close_due_early_reversion_v25_trades
from bot.early_reversion_v3 import close_due_early_reversion_v3_trades
from bot.paper_trader import settle_due_trades

logger = logging.getLogger(__name__)

_OPEN_TRADE_QUERIES: tuple[tuple[str, str], ...] = (
    ("virtual_trades", "open"),
    ("early_reversion_trades", "open"),
    ("early_reversion_v2_trades", "open"),
    ("early_reversion_v25_trades", "open"),
    ("early_reversion_v3_trades", "open"),
)


def count_open_trades(conn: sqlite3.Connection) -> int:
    total = 0
    for table, status in _OPEN_TRADE_QUERIES:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE status = ?",
            (status,),
        ).fetchone()
        total += int(row["c"])
    return total


def close_expired_open_trades(conn: sqlite3.Connection, now_ts: int) -> int:
    """Close/settle open trades whose market window has ended."""
    closed = 0
    closed += close_due_early_reversion_trades(conn, now_ts)
    closed += close_due_early_reversion_v2_trades(conn, now_ts)
    closed += close_due_early_reversion_v25_trades(conn, now_ts)
    closed += close_due_early_reversion_v3_trades(conn, now_ts)
    closed += settle_due_trades(conn)
    return closed


def recover_open_trades(conn: sqlite3.Connection, now_ts: int | None = None) -> int:
    """
    Count open trades and close any whose market has already ended.

    Returns the number of open trades found at startup (before closing expired).
    """
    if now_ts is None:
        now_ts = int(time.time())

    open_count = count_open_trades(conn)
    unrecorded = reconcile_unrecorded_entry_intents(conn)
    if unrecorded:
        logger.critical(
            "Recovered %s unrecorded live entry intent(s) into SQLite",
            unrecorded,
        )
    if open_count:
        expired_closed = close_expired_open_trades(conn, now_ts)
        if expired_closed:
            logger.info(
                "Startup recovery closed %s expired trade(s); %s still open",
                expired_closed,
                count_open_trades(conn),
            )
    stuck_exits = reconcile_stuck_exits(conn, now_ts)
    if stuck_exits:
        logger.warning(
            "Startup exit recovery reconciled %s stuck open position(s)",
            stuck_exits,
        )
    return open_count


def run_startup_recovery() -> int:
    """Initialize DB, recover open trades, and return how many were found."""
    init_db()
    with connect() as conn:
        open_count = recover_open_trades(conn)
        conn.commit()
    logger.info("Recovered %s open trades from database.", open_count)
    return open_count
