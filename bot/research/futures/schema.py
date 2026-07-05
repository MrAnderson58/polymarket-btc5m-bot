"""Futures research tables — additive, separate from raw telegram messages."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

SIGNALS_TABLE = "futures_research_signals"
TARGETS_TABLE = "futures_signal_targets"
PARSE_AUDIT_TABLE = "futures_signal_parse_audit"
SNAPSHOTS_TABLE = "futures_signal_snapshots"
OUTCOMES_TABLE = "futures_signal_outcomes"
RECOMMENDATIONS_TABLE = "futures_signal_recommendations"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {SIGNALS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            message_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            symbol TEXT,
            side TEXT,
            entry_min REAL,
            entry_max REAL,
            stop_loss REAL,
            leverage REAL,
            timeframe TEXT,
            confidence REAL,
            raw_text TEXT NOT NULL,
            parser_confidence REAL NOT NULL DEFAULT 0.0,
            parser_version TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source, message_id, parser_version)
        );
        CREATE INDEX IF NOT EXISTS idx_futures_signals_ts ON {SIGNALS_TABLE}(timestamp);
        CREATE INDEX IF NOT EXISTS idx_futures_signals_source ON {SIGNALS_TABLE}(source);
        CREATE INDEX IF NOT EXISTS idx_futures_signals_symbol ON {SIGNALS_TABLE}(symbol);

        CREATE TABLE IF NOT EXISTS {TARGETS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            level_index INTEGER NOT NULL,
            price REAL NOT NULL,
            label TEXT,
            FOREIGN KEY (signal_id) REFERENCES {SIGNALS_TABLE}(id)
        );
        CREATE INDEX IF NOT EXISTS idx_futures_targets_signal ON {TARGETS_TABLE}(signal_id);

        CREATE TABLE IF NOT EXISTS {PARSE_AUDIT_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            message_id TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            fields_found TEXT,
            errors TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source, message_id, parser_version)
        );

        CREATE TABLE IF NOT EXISTS {SNAPSHOTS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL UNIQUE,
            snapshot_ts INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            coverage_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (signal_id) REFERENCES {SIGNALS_TABLE}(id)
        );

        CREATE TABLE IF NOT EXISTS {OUTCOMES_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            horizon TEXT NOT NULL,
            forward_return REAL,
            direction_correct INTEGER,
            mfe REAL,
            mae REAL,
            tp_first INTEGER,
            sl_first INTEGER,
            time_to_tp INTEGER,
            time_to_sl INTEGER,
            pnl_explicit REAL,
            pnl_standardized REAL,
            evaluated_at INTEGER NOT NULL,
            UNIQUE(signal_id, horizon),
            FOREIGN KEY (signal_id) REFERENCES {SIGNALS_TABLE}(id)
        );
        CREATE INDEX IF NOT EXISTS idx_futures_outcomes_signal ON {OUTCOMES_TABLE}(signal_id);

        CREATE TABLE IF NOT EXISTS {RECOMMENDATIONS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            model_version TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            confidence REAL,
            reasons_json TEXT,
            created_at INTEGER NOT NULL,
            outcome_horizon TEXT,
            outcome_pnl REAL,
            FOREIGN KEY (signal_id) REFERENCES {SIGNALS_TABLE}(id)
        );
        CREATE INDEX IF NOT EXISTS idx_futures_recs_signal ON {RECOMMENDATIONS_TABLE}(signal_id);
    """)


def insert_signal(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    cur = conn.execute(
        f"""
        INSERT OR IGNORE INTO {SIGNALS_TABLE} (
            source, message_id, timestamp, symbol, side,
            entry_min, entry_max, stop_loss, leverage, timeframe,
            confidence, raw_text, parser_confidence, parser_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["source"], row["message_id"], row["timestamp"],
            row.get("symbol"), row.get("side"),
            row.get("entry_min"), row.get("entry_max"), row.get("stop_loss"),
            row.get("leverage"), row.get("timeframe"), row.get("confidence"),
            row["raw_text"], row.get("parser_confidence", 0.0), row["parser_version"],
        ),
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    existing = conn.execute(
        f"""
        SELECT id FROM {SIGNALS_TABLE}
        WHERE source = ? AND message_id = ? AND parser_version = ?
        """,
        (row["source"], row["message_id"], row["parser_version"]),
    ).fetchone()
    return int(existing["id"]) if existing else 0
