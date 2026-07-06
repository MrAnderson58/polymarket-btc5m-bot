"""Research-only tables for market behavior analysis."""

from __future__ import annotations

import sqlite3

SUMMARY_TABLE = "mb_market_summary"
LATE_WINDOW_TABLE = "mb_late_window"
TP_PROBABILITY_TABLE = "mb_tp_probability"
EDGE_STATISTICS_TABLE = "mb_edge_statistics"


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create additive research tables. Does not modify existing bot tables."""
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {SUMMARY_TABLE} (
            market_slug TEXT PRIMARY KEY,
            window_start_ts INTEGER NOT NULL,
            strike REAL,
            final_btc REAL NOT NULL,
            btc_delta_final REAL,
            winning_side TEXT NOT NULL,
            yes_bid_min REAL,
            yes_bid_max REAL,
            yes_ask_min REAL,
            yes_ask_max REAL,
            no_bid_min REAL,
            no_bid_max REAL,
            no_ask_min REAL,
            no_ask_max REAL,
            observation_count INTEGER NOT NULL,
            analyzed_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mb_summary_window
            ON {SUMMARY_TABLE}(window_start_ts);

        CREATE TABLE IF NOT EXISTS {LATE_WINDOW_TABLE} (
            market_slug TEXT NOT NULL,
            seconds_bucket INTEGER NOT NULL,
            obs_timestamp INTEGER,
            seconds_left_actual INTEGER,
            btc_price REAL,
            btc_delta_vs_strike REAL,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            btc_move_to_close REAL,
            yes_ask_move_to_close REAL,
            no_ask_move_to_close REAL,
            PRIMARY KEY (market_slug, seconds_bucket)
        );
        CREATE INDEX IF NOT EXISTS idx_mb_late_bucket
            ON {LATE_WINDOW_TABLE}(seconds_bucket);

        CREATE TABLE IF NOT EXISTS {TP_PROBABILITY_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            side TEXT NOT NULL,
            entry_bucket TEXT NOT NULL,
            entry_price_mid REAL NOT NULL,
            tp_level REAL NOT NULL,
            sample_count INTEGER NOT NULL,
            reach_count INTEGER NOT NULL,
            reach_probability REAL NOT NULL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(side, entry_bucket, tp_level)
        );
        CREATE INDEX IF NOT EXISTS idx_mb_tp_side_bucket
            ON {TP_PROBABILITY_TABLE}(side, entry_bucket);

        CREATE TABLE IF NOT EXISTS {EDGE_STATISTICS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction TEXT NOT NULL,
            entry_bucket TEXT NOT NULL,
            btc_delta_bucket TEXT NOT NULL,
            seconds_left_bucket TEXT NOT NULL,
            spread_bucket TEXT NOT NULL,
            samples INTEGER NOT NULL,
            tp55_prob REAL,
            tp60_prob REAL,
            tp65_prob REAL,
            tp70_prob REAL,
            tp75_prob REAL,
            avg_final_delta REAL,
            avg_move_to_close REAL,
            avg_spread REAL,
            avg_max_excursion REAL,
            avg_adverse_excursion REAL,
            avg_entry_price REAL,
            ev_tp55 REAL,
            ev_tp60 REAL,
            ev_tp65 REAL,
            ev_tp70 REAL,
            ev_tp75 REAL,
            expected_profit_tp60 REAL,
            expected_loss_tp60 REAL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(direction, entry_bucket, btc_delta_bucket, seconds_left_bucket, spread_bucket)
        );
        CREATE INDEX IF NOT EXISTS idx_mb_edge_direction
            ON {EDGE_STATISTICS_TABLE}(direction);
        CREATE INDEX IF NOT EXISTS idx_mb_edge_samples
            ON {EDGE_STATISTICS_TABLE}(samples DESC);
    """)
