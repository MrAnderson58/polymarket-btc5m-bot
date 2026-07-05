"""Multi-timeframe snapshot storage — observe-only collector DB."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

TABLE = "multi_timeframe_snapshots"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            btc_price REAL,
            market_5m_slug TEXT,
            market_5m_yes_ask REAL,
            market_5m_no_ask REAL,
            market_15m_slug TEXT,
            market_15m_yes_bid REAL,
            market_15m_yes_ask REAL,
            market_15m_no_bid REAL,
            market_15m_no_ask REAL,
            market_15m_strike REAL,
            market_15m_seconds_left INTEGER,
            market_1h_slug TEXT,
            market_1h_yes_bid REAL,
            market_1h_yes_ask REAL,
            market_1h_no_bid REAL,
            market_1h_no_ask REAL,
            market_1h_strike REAL,
            market_1h_seconds_left INTEGER,
            market_daily_slug TEXT,
            market_daily_yes_bid REAL,
            market_daily_yes_ask REAL,
            market_daily_no_bid REAL,
            market_daily_no_ask REAL,
            market_daily_strike REAL,
            market_daily_seconds_left INTEGER,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mtf_snapshots_ts
            ON {TABLE}(timestamp);
        CREATE INDEX IF NOT EXISTS idx_mtf_snapshots_15m_slug
            ON {TABLE}(market_15m_slug);
    """)


def insert_snapshot(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        f"""
        INSERT INTO {TABLE} (
            timestamp, btc_price, market_5m_slug,
            market_5m_yes_ask, market_5m_no_ask,
            market_15m_slug, market_15m_yes_bid, market_15m_yes_ask,
            market_15m_no_bid, market_15m_no_ask, market_15m_strike, market_15m_seconds_left,
            market_1h_slug, market_1h_yes_bid, market_1h_yes_ask,
            market_1h_no_bid, market_1h_no_ask, market_1h_strike, market_1h_seconds_left,
            market_daily_slug, market_daily_yes_bid, market_daily_yes_ask,
            market_daily_no_bid, market_daily_no_ask, market_daily_strike, market_daily_seconds_left,
            raw_json
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            row.get("timestamp"),
            row.get("btc_price"),
            row.get("market_5m_slug"),
            row.get("market_5m_yes_ask"),
            row.get("market_5m_no_ask"),
            row.get("market_15m_slug"),
            row.get("market_15m_yes_bid"),
            row.get("market_15m_yes_ask"),
            row.get("market_15m_no_bid"),
            row.get("market_15m_no_ask"),
            row.get("market_15m_strike"),
            row.get("market_15m_seconds_left"),
            row.get("market_1h_slug"),
            row.get("market_1h_yes_bid"),
            row.get("market_1h_yes_ask"),
            row.get("market_1h_no_bid"),
            row.get("market_1h_no_ask"),
            row.get("market_1h_strike"),
            row.get("market_1h_seconds_left"),
            row.get("market_daily_slug"),
            row.get("market_daily_yes_bid"),
            row.get("market_daily_yes_ask"),
            row.get("market_daily_no_bid"),
            row.get("market_daily_no_ask"),
            row.get("market_daily_strike"),
            row.get("market_daily_seconds_left"),
            json.dumps(row.get("raw_json")) if row.get("raw_json") else None,
        ),
    )


def snapshot_at_or_before(conn: sqlite3.Connection, ts: int) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT * FROM {TABLE}
        WHERE timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (ts,),
    ).fetchone()


def snapshot_nearest(conn: sqlite3.Connection, ts: int, window_sec: int = 30) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT *, ABS(timestamp - ?) AS dist
        FROM {TABLE}
        WHERE timestamp BETWEEN ? AND ?
        ORDER BY dist ASC, timestamp DESC
        LIMIT 1
        """,
        (ts, ts - window_sec, ts + window_sec),
    ).fetchone()
