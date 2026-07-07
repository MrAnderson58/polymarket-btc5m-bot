"""Research-only tables and persistence for strategy simulation."""

from __future__ import annotations

import json
import sqlite3

from bot.research.strategy_simulator.statistics import SimulationStats


SIM_RESULTS_TABLE = "ss_simulation_results"
DISCOVERED_TABLE = "ss_discovered_strategies"
SHADOW_CANDIDATES_TABLE = "ss_shadow_candidates"
FORWARD_CANDIDATES_TABLE = "ss_forward_candidates"
FORWARD_SIGNALS_TABLE = "ss_forward_signals"


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

        CREATE TABLE IF NOT EXISTS {SHADOW_CANDIDATES_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_fp TEXT NOT NULL UNIQUE,
            parameters_json TEXT NOT NULL,
            train_metrics_json TEXT NOT NULL,
            validation_metrics_json TEXT NOT NULL,
            test_metrics_json TEXT NOT NULL,
            bootstrap_metrics_json TEXT NOT NULL,
            cost_stress_metrics_json TEXT NOT NULL,
            qualified INTEGER NOT NULL DEFAULT 1,
            enabled_shadow INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)


def ensure_forward_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {FORWARD_CANDIDATES_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_fp TEXT NOT NULL UNIQUE,
            parameters_json TEXT NOT NULL,
            family_label TEXT NOT NULL,
            family_size INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            registered_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS {FORWARD_SIGNALS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_fp TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            signal_ts INTEGER NOT NULL,
            entry_ask REAL NOT NULL,
            btc_delta REAL,
            seconds_left INTEGER NOT NULL,
            spread REAL,
            tp REAL NOT NULL,
            max_bid_after REAL,
            final_bid REAL,
            tp_reached INTEGER NOT NULL DEFAULT 0,
            simulated_pnl REAL NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(strategy_fp, market_slug, signal_ts)
        );
        CREATE INDEX IF NOT EXISTS idx_ss_forward_signals_fp
            ON {FORWARD_SIGNALS_TABLE}(strategy_fp, signal_ts DESC);
    """)


def register_forward_candidates(
    conn: sqlite3.Connection,
    payloads: list[dict],
    *,
    replace: bool = True,
) -> list[str]:
    ensure_forward_tables(conn)
    if replace:
        conn.execute(f"DELETE FROM {FORWARD_CANDIDATES_TABLE}")
    fps: list[str] = []
    for p in payloads:
        conn.execute(
            f"""
            INSERT INTO {FORWARD_CANDIDATES_TABLE} (
                strategy_fp, parameters_json, family_label, family_size, active
            ) VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(strategy_fp) DO UPDATE SET
                parameters_json=excluded.parameters_json,
                family_label=excluded.family_label,
                family_size=excluded.family_size,
                active=1
            """,
            (
                p["strategy_fp"],
                p["parameters_json"],
                p["family_label"],
                int(p.get("family_size", 1)),
            ),
        )
        fps.append(p["strategy_fp"])
    return fps


def list_active_forward_candidates(conn: sqlite3.Connection) -> list[dict]:
    ensure_forward_tables(conn)
    rows = conn.execute(
        f"""
        SELECT * FROM {FORWARD_CANDIDATES_TABLE}
        WHERE active = 1
        ORDER BY id ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def insert_forward_signal(conn: sqlite3.Connection, rec) -> None:
    ensure_forward_tables(conn)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {FORWARD_SIGNALS_TABLE} (
            strategy_fp, market_slug, signal_ts, entry_ask, btc_delta,
            seconds_left, spread, tp, max_bid_after, final_bid,
            tp_reached, simulated_pnl
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.strategy_fp,
            rec.market_slug,
            rec.signal_ts,
            rec.entry_ask,
            rec.btc_delta,
            rec.seconds_left,
            rec.spread,
            rec.tp,
            rec.max_bid_after,
            rec.final_bid,
            1 if rec.tp_reached else 0,
            rec.simulated_pnl,
        ),
    )


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


def store_shadow_candidates(
    conn: sqlite3.Connection,
    results: list,
    *,
    replace: bool = True,
) -> list[int]:
    if replace:
        conn.execute(f"DELETE FROM {SHADOW_CANDIDATES_TABLE}")
    ids: list[int] = []
    for r in results:
        cost_json = {k: v.to_dict() for k, v in r.cost_metrics.items()}
        cur = conn.execute(
            f"""
            INSERT INTO {SHADOW_CANDIDATES_TABLE} (
                strategy_fp, parameters_json,
                train_metrics_json, validation_metrics_json, test_metrics_json,
                bootstrap_metrics_json, cost_stress_metrics_json,
                qualified, enabled_shadow, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, datetime('now'))
            ON CONFLICT(strategy_fp) DO UPDATE SET
                parameters_json=excluded.parameters_json,
                train_metrics_json=excluded.train_metrics_json,
                validation_metrics_json=excluded.validation_metrics_json,
                test_metrics_json=excluded.test_metrics_json,
                bootstrap_metrics_json=excluded.bootstrap_metrics_json,
                cost_stress_metrics_json=excluded.cost_stress_metrics_json,
                qualified=1,
                created_at=datetime('now')
            """,
            (
                r.fingerprint,
                r.strategy.to_json(),
                json.dumps(r.train.to_dict()),
                json.dumps(r.validation.to_dict()),
                json.dumps(r.test.to_dict()),
                json.dumps(r.bootstrap.to_dict()),
                json.dumps(cost_json),
            ),
        )
        row_id = cur.lastrowid
        if row_id:
            ids.append(int(row_id))
    conn.commit()
    return ids


def list_shadow_candidates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT id, strategy_fp, parameters_json, qualified, enabled_shadow, created_at
        FROM {SHADOW_CANDIDATES_TABLE}
        ORDER BY id
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def enable_shadow_candidate(conn: sqlite3.Connection, strategy_id: int, *, enabled: bool) -> None:
    conn.execute(
        f"UPDATE {SHADOW_CANDIDATES_TABLE} SET enabled_shadow=? WHERE id=?",
        (1 if enabled else 0, strategy_id),
    )
    conn.commit()
