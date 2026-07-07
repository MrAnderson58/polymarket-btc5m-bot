"""Tests for split diagnostics and cost model invariants."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.strategy_simulator.cost_model import (
    BASE_COSTS,
    IDEAL_COSTS,
    STRESS_COSTS,
    apply_cost_model_filtered,
    apply_costs_same_trade_set,
)
from bot.research.strategy_simulator.data_quality import analyze_split_data_quality
from bot.research.strategy_simulator.opportunity import analyze_split_opportunity
from bot.research.strategy_simulator.regime import observation_density_deciles
from bot.research.strategy_simulator.rolling_oos import build_rolling_folds
from bot.research.strategy_simulator.simulator import VirtualTrade, build_market_context
from bot.research.strategy_simulator.split_audit import audit_split
from bot.research.strategy_simulator.split_diagnostics import run_split_diagnostics
from bot.research.strategy_simulator.splits import split_markets_chronological
from bot.research.strategy_simulator.statistics import compute_stats
from bot.research.strategy_simulator.strategies import Strategy


def _trade(slug: str, ts: int, pnl: float = 0.1, spread: float = 0.03) -> VirtualTrade:
    return VirtualTrade(
        market_slug=slug,
        strategy_fp="fp",
        direction="YES",
        entry_ts=ts,
        exit_ts=ts + 10,
        entry_price=0.25,
        exit_price=0.25 + pnl,
        pnl=pnl,
        won=pnl > 0,
        holding_seconds=10,
        btc_delta_at_entry=10.0,
        seconds_left_at_entry=120,
        spread_at_entry=spread,
    )


def _paths(n: int) -> dict[str, list[dict]]:
    paths = {}
    for i in range(n):
        ws = 1_700_000_000 + i * 400
        obs = []
        for j in range(25):
            ts = ws + j * 12
            sl = 300 - j * 12
            yes_ask = 0.28 + j * 0.01
            yes_bid = yes_ask - 0.02
            obs.append({
                "timestamp": ts,
                "window_start_ts": ws,
                "seconds_left": sl,
                "seconds_from_start": j * 12,
                "btc_price": 100.0 + j * 0.3,
                "strike": 100.0,
                "delta": j * 0.3,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": 1.0 - yes_ask,
                "no_ask": 1.0 - yes_bid,
            })
        paths[f"btc-updown-5m-{i}"] = obs
    return paths


class SplitChronologyTest(unittest.TestCase):
    def test_no_overlap_and_ordering(self) -> None:
        paths = _paths(30)
        split = split_markets_chronological(paths)
        audits = audit_split(split, paths)
        self.assertTrue(audits["GLOBAL"].ok)

    def test_rolling_folds_no_lookahead(self) -> None:
        paths = _paths(30)
        folds = build_rolling_folds(paths, n_folds=5)
        self.assertGreaterEqual(len(folds), 5)
        for train, test in folds:
            self.assertFalse(set(train) & set(test))


class CostMonotonicityTest(unittest.TestCase):
    def test_same_trade_set_monotonic(self) -> None:
        trades = [_trade(f"m{i}", i, pnl=0.05 + i * 0.01) for i in range(20)]
        strategy = Strategy(direction="YES", max_entry=0.30, tp=0.60)
        ideal = compute_stats(strategy, apply_costs_same_trade_set(trades, IDEAL_COSTS))
        base = compute_stats(strategy, apply_costs_same_trade_set(trades, BASE_COSTS))
        stress = compute_stats(strategy, apply_costs_same_trade_set(trades, STRESS_COSTS))
        self.assertGreaterEqual(ideal.expected_value, base.expected_value)
        self.assertGreaterEqual(base.expected_value, stress.expected_value)

    def test_stress_filter_can_change_trade_set(self) -> None:
        wide = [_trade("m1", 1, spread=0.04)]
        narrow = [_trade("m2", 2, spread=0.01)]
        trades = wide + narrow
        filtered = apply_cost_model_filtered(trades, STRESS_COSTS, seed=42)
        self.assertLess(len(filtered), len(trades))


class OpportunityFunnelTest(unittest.TestCase):
    def test_funnel_counts(self) -> None:
        paths = _paths(5)
        strategy = Strategy(
            direction="YES",
            max_entry=0.35,
            min_delta=0.0,
            max_spread=0.05,
            min_seconds_left=60,
            tp=0.60,
        )
        ctx = {s: build_market_context(s, p) for s, p in paths.items()}
        funnel = analyze_split_opportunity(ctx, strategy)
        self.assertEqual(funnel.markets_scanned, 5)
        self.assertGreaterEqual(funnel.entry_opportunity, 0)


class ObservationDensityTest(unittest.TestCase):
    def test_deciles_sum_to_markets(self) -> None:
        paths = _paths(25)
        deciles = observation_density_deciles(paths)
        self.assertEqual(len(deciles), 10)
        self.assertEqual(sum(n for _, n, _ in deciles), 25)


class SplitDiagnosticsIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "diag.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed(self, conn: sqlite3.Connection, slug: str, ws: int) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS v4_shadow_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                window_start_ts INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                seconds_from_start INTEGER NOT NULL,
                seconds_left INTEGER NOT NULL,
                btc_price REAL NOT NULL,
                strike REAL, delta REAL,
                yes_bid REAL, yes_ask REAL, no_bid REAL, no_ask REAL,
                spread REAL, created_at TEXT
            );
        """)
        for j in range(25):
            ts = ws + j * 12
            sl = 300 - j * 12
            yes_ask = 0.28 + j * 0.01
            yes_bid = yes_ask - 0.02
            conn.execute(
                """
                INSERT INTO v4_shadow_observations (
                    market_slug, window_start_ts, timestamp, seconds_from_start,
                    seconds_left, btc_price, strike, delta,
                    yes_bid, yes_ask, no_bid, no_ask, spread
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug, ws, ts, j * 12, sl, 100.0 + j * 0.3, 100.0, j * 0.3,
                    yes_bid, yes_ask, 1.0 - yes_ask, 1.0 - yes_bid, 0.02,
                ),
            )

    def test_diagnostics_reproducible(self) -> None:
        with connect(self.db_path) as conn:
            for i in range(15):
                self._seed(conn, f"btc-updown-5m-{i}", 1_700_000_000 + i * 400)
            conn.commit()
            from bot.research.strategy_simulator.storage import ensure_tables
            ensure_tables(conn)
            r1 = run_split_diagnostics(conn, min_obs=20, top_n=3, min_trades=1)
            r2 = run_split_diagnostics(conn, min_obs=20, top_n=3, min_trades=1)
        self.assertEqual(r1.split.train, r2.split.train)
        self.assertEqual(len(r1.strategies), len(r2.strategies))


if __name__ == "__main__":
    unittest.main()
