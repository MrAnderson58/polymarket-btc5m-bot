"""Bidirectional Momentum V1.2 Shadow — parallel observe-only virtual trading.

Runs alongside V1.1 on the same markets. Separate tables, separate config.
Never calls execution or places orders.
"""

from __future__ import annotations

import logging
import sqlite3

from bot.strategy.bidirectional_momentum import DirectionDecision
from bot.strategy.bidirectional_v12_config import V12_CANDIDATE_SPEC

logger = logging.getLogger(__name__)

V12_TABLE_PREFIX = "bidirectional_shadow_v12"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {V12_TABLE_PREFIX}_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            decision TEXT NOT NULL,
            confidence REAL,
            probability_yes REAL,
            probability_no REAL,
            regime TEXT,
            reason TEXT,
            btc_move_30s REAL,
            entry_price REAL,
            filter_passed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS {V12_TABLE_PREFIX}_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            window_start_ts INTEGER,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            entry_regime TEXT,
            entry_confidence REAL,
            entry_reason TEXT,
            max_price_seen REAL,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            holding_time_seconds REAL,
            created_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_bidi_v12_obs_market
            ON {V12_TABLE_PREFIX}_observations(market_slug, timestamp);
        CREATE INDEX IF NOT EXISTS idx_bidi_v12_trades_status
            ON {V12_TABLE_PREFIX}_trades(status);
    """)
    _ensure_unique_market_index(conn)


def _ensure_unique_market_index(conn: sqlite3.Connection) -> None:
    has_dupes = conn.execute(
        f"""
        SELECT 1 FROM {V12_TABLE_PREFIX}_trades
        GROUP BY market_slug HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if has_dupes is None:
        conn.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bidi_v12_trades_market_unique
            ON {V12_TABLE_PREFIX}_trades(market_slug)
            """
        )


def record_observation(
    conn: sqlite3.Connection,
    decision: DirectionDecision,
    market_slug: str,
    timestamp: int,
    entry_price: float | None = None,
    filter_passed: bool = False,
) -> None:
    conn.execute(
        f"""INSERT INTO {V12_TABLE_PREFIX}_observations
           (market_slug, timestamp, decision, confidence, probability_yes,
            probability_no, regime, reason, btc_move_30s, entry_price, filter_passed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            market_slug,
            timestamp,
            decision.decision,
            decision.confidence,
            decision.probability_yes,
            decision.probability_no,
            decision.regime,
            decision.reason,
            decision.features.get("btc_move_30s"),
            entry_price,
            1 if filter_passed else 0,
        ),
    )


def open_shadow_trade(
    conn: sqlite3.Connection,
    decision: DirectionDecision,
    market_slug: str,
    window_start_ts: int,
    entry_price: float,
    entry_ts: int,
) -> int | None:
    if has_shadow_trade(conn, market_slug):
        return None
    cur = conn.execute(
        f"""INSERT OR IGNORE INTO {V12_TABLE_PREFIX}_trades
           (market_slug, window_start_ts, side, status, entry_price, entry_ts,
            entry_regime, entry_confidence, entry_reason, max_price_seen)
           VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)""",
        (
            market_slug,
            window_start_ts,
            decision.decision,
            entry_price,
            entry_ts,
            decision.regime,
            decision.confidence,
            decision.reason,
            entry_price,
        ),
    )
    if cur.rowcount == 0:
        return None
    return cur.lastrowid


def close_shadow_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    pnl_pct: float,
    holding_time: float,
) -> None:
    conn.execute(
        f"""UPDATE {V12_TABLE_PREFIX}_trades
           SET status='closed', exit_price=?, exit_reason=?, pnl_pct=?,
               holding_time_seconds=?, closed_at=datetime('now')
           WHERE id=?""",
        (exit_price, exit_reason, pnl_pct, holding_time, trade_id),
    )


def get_open_shadow_trade(conn: sqlite3.Connection, market_slug: str) -> dict | None:
    row = conn.execute(
        f"SELECT * FROM {V12_TABLE_PREFIX}_trades WHERE market_slug=? AND status='open' LIMIT 1",
        (market_slug,),
    ).fetchone()
    return dict(row) if row else None


def has_shadow_trade(conn: sqlite3.Connection, market_slug: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {V12_TABLE_PREFIX}_trades WHERE market_slug=? LIMIT 1",
        (market_slug,),
    ).fetchone()
    return row is not None


def shadow_state(conn: sqlite3.Connection) -> dict:
    ensure_tables(conn)
    spec = V12_CANDIDATE_SPEC

    closed_n = conn.execute(
        f"SELECT COUNT(*) FROM {V12_TABLE_PREFIX}_trades WHERE status='closed'"
    ).fetchone()[0]

    row = conn.execute(f"""
        SELECT SUM(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE 0 END) as gp,
               SUM(CASE WHEN pnl_pct <= 0 THEN ABS(pnl_pct) ELSE 0 END) as gl
        FROM {V12_TABLE_PREFIX}_trades WHERE status='closed'
    """).fetchone()
    gp = row["gp"] or 0
    gl = row["gl"] or 0
    pf = gp / gl if gl else 0.0

    return {
        "version": "V1.2",
        "spec_name": spec.name,
        "trades_closed": closed_n,
        "pf": round(pf, 3),
        "status": "COLLECTING" if closed_n < 100 else "VALIDATING",
    }
