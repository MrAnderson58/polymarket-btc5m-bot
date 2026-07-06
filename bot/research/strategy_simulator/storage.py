"""Research-only tables and persistence for strategy simulation."""

from __future__ import annotations

import sqlite3

from bot.research.strategy_simulator.statistics import SimulationStats

SIM_RESULTS_TABLE = "ss_simulation_results"
DISCOVERED_TABLE = "ss_discovered_strategies"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {SIM_RESULTS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_fp TEXT NOT NULL,
            direction TEXT NOT NULL,
            max_entry REAL NOT NULL,
            min_delta REAL,
            max_delta REAL,
            max_spread REAL NOT NULL,
            min_seconds_left INTEGER NOT NULL,
            tp REAL NOT NULL,
            trades INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            average_win REAL NOT NULL,
            average_loss REAL NOT NULL,
            profit_factor REAL NOT NULL,
            expected_value REAL NOT NULL,
            average_holding_seconds REAL NOT NULL,
            max_drawdown REAL NOT NULL,
            sharpe REAL,
            longest_losing_streak INTEGER NOT NULL,
            longest_winning_streak INTEGER NOT NULL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(strategy_fp)
        );
        CREATE INDEX IF NOT EXISTS idx_ss_sim_ev ON {SIM_RESULTS_TABLE}(expected_value DESC);

        CREATE TABLE IF NOT EXISTS {DISCOVERED_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rank INTEGER NOT NULL,
            strategy_fp TEXT NOT NULL,
            direction TEXT NOT NULL,
            max_entry REAL NOT NULL,
            min_delta REAL,
            max_delta REAL,
            max_spread REAL NOT NULL,
            min_seconds_left INTEGER NOT NULL,
            tp REAL NOT NULL,
            trades INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            profit_factor REAL NOT NULL,
            expected_value REAL NOT NULL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(strategy_fp)
        );
    """)


def store_simulation_stats(conn: sqlite3.Connection, stats: SimulationStats) -> None:
    s = stats.strategy
    conn.execute(
        f"""
        INSERT INTO {SIM_RESULTS_TABLE} (
            strategy_fp, direction, max_entry, min_delta, max_delta,
            max_spread, min_seconds_left, tp,
            trades, wins, losses, win_rate, average_win, average_loss,
            profit_factor, expected_value, average_holding_seconds,
            max_drawdown, sharpe, longest_losing_streak, longest_winning_streak,
            computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(strategy_fp) DO UPDATE SET
            trades=excluded.trades, wins=excluded.wins, losses=excluded.losses,
            win_rate=excluded.win_rate, average_win=excluded.average_win,
            average_loss=excluded.average_loss, profit_factor=excluded.profit_factor,
            expected_value=excluded.expected_value,
            average_holding_seconds=excluded.average_holding_seconds,
            max_drawdown=excluded.max_drawdown, sharpe=excluded.sharpe,
            longest_losing_streak=excluded.longest_losing_streak,
            longest_winning_streak=excluded.longest_winning_streak,
            computed_at=datetime('now')
        """,
        (
            stats.fingerprint, s.direction, s.max_entry, s.min_delta, s.max_delta,
            s.max_spread, s.min_seconds_left, s.tp,
            stats.trades, stats.wins, stats.losses, stats.win_rate,
            stats.average_win, stats.average_loss, stats.profit_factor,
            stats.expected_value, stats.average_holding_seconds,
            stats.max_drawdown, stats.sharpe,
            stats.longest_losing_streak, stats.longest_winning_streak,
        ),
    )
    conn.commit()


def store_discovered(conn: sqlite3.Connection, ranked: list[SimulationStats]) -> None:
    conn.execute(f"DELETE FROM {DISCOVERED_TABLE}")
    for i, stats in enumerate(ranked, start=1):
        s = stats.strategy
        conn.execute(
            f"""
            INSERT INTO {DISCOVERED_TABLE} (
                rank, strategy_fp, direction, max_entry, min_delta, max_delta,
                max_spread, min_seconds_left, tp,
                trades, win_rate, profit_factor, expected_value, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                i, stats.fingerprint, s.direction, s.max_entry, s.min_delta, s.max_delta,
                s.max_spread, s.min_seconds_left, s.tp,
                stats.trades, stats.win_rate, stats.profit_factor, stats.expected_value,
            ),
        )
    conn.commit()
