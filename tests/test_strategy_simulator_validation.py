"""Validation, walk-forward, bootstrap, and dedup tests."""

from __future__ import annotations

import ast
import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.strategy_simulator.bootstrap import bootstrap_market_metrics
from bot.research.strategy_simulator.cost_model import (
    BASE_COSTS,
    IDEAL_COSTS,
    apply_costs_same_trade_set,
)
from bot.research.strategy_simulator.deduplication import deduplicate_by_trade_set, trade_set_identity
from bot.research.strategy_simulator.grid import generate_discovery_grid
from bot.research.strategy_simulator.simulator import VirtualTrade
from bot.research.strategy_simulator.splits import split_markets_chronological
from bot.research.strategy_simulator.statistics import SimulationStats, compute_stats
from bot.research.strategy_simulator.strategies import Strategy, StrategyValidationError
from bot.research.strategy_simulator.walk_forward import run_walk_forward


def _trade(slug: str, ts: int, pnl: float = 0.1, fp: str = "fp") -> VirtualTrade:
    return VirtualTrade(
        market_slug=slug,
        strategy_fp=fp,
        direction="YES",
        entry_ts=ts,
        exit_ts=ts + 10,
        entry_price=0.25,
        exit_price=0.35,
        pnl=pnl,
        won=pnl > 0,
        holding_seconds=10,
        btc_delta_at_entry=10.0,
        seconds_left_at_entry=120,
        spread_at_entry=0.02,
    )


class DeltaSemanticsTest(unittest.TestCase):
    def test_impossible_no_delta_interval_rejected(self) -> None:
        with self.assertRaises(StrategyValidationError):
            Strategy(
                direction="NO",
                max_entry=0.30,
                min_delta=0.0,
                max_delta=0.0,
                max_spread=0.02,
                min_seconds_left=60,
                tp=0.60,
            )

    def test_grid_never_generates_min_gte_max(self) -> None:
        for s in generate_discovery_grid():
            if s.min_delta is not None and s.max_delta is not None:
                self.assertLess(s.min_delta, s.max_delta)

    def test_no_direction_positive_bounds_rejected(self) -> None:
        self.assertFalse(Strategy.is_valid(
            direction="NO", max_entry=0.30, min_delta=25, max_delta=None,
            max_spread=0.02, min_seconds_left=60, tp=0.60,
        ))

    def test_report_predicates_match_simulator(self) -> None:
        s = Strategy(
            direction="NO",
            max_entry=0.25,
            min_delta=-60.0,
            max_delta=-25.0,
            max_spread=0.02,
            min_seconds_left=60,
            tp=0.65,
        )
        lines = s.predicate_lines()
        self.assertIn("btc_delta >= -60.00", lines)
        self.assertIn("btc_delta <= -25.00", lines)
        self.assertTrue(s.matches_btc_delta(-30.0))
        self.assertFalse(s.matches_btc_delta(0.0))

    def test_legacy_display_would_mislead_on_zero_bounds(self) -> None:
        """Regression: min=0 max=0 displayed as >0 and <0 but meant >=0 and <=0."""
        with self.assertRaises(StrategyValidationError):
            Strategy(
                direction="NO",
                max_entry=0.30,
                min_delta=0,
                max_delta=0,
                tp=0.60,
            )


class DeduplicationTest(unittest.TestCase):
    def test_equivalent_trade_sets_deduplicated(self) -> None:
        trades = [_trade("m1", 100), _trade("m2", 200)]
        identity = trade_set_identity(trades)
        s1 = Strategy(direction="YES", max_entry=0.30, max_spread=0.01, tp=0.60)
        s2 = Strategy(direction="YES", max_entry=0.30, max_spread=0.05, tp=0.60)
        stats1 = compute_stats(s1, trades)
        stats2 = compute_stats(s2, trades)
        families = deduplicate_by_trade_set([stats1, stats2], {
            stats1.fingerprint: trades,
            stats2.fingerprint: trades,
        })
        self.assertEqual(len(families), 1)
        self.assertEqual(families[0].family_size, 2)
        self.assertEqual(families[0].representative.strategy.max_spread, 0.01)


class SplitTest(unittest.TestCase):
    def _paths(self, n: int) -> dict[str, list[dict]]:
        paths = {}
        for i in range(n):
            ws = 1_700_000_000 + i * 300
            paths[f"m-{i}"] = [{"timestamp": ws, "window_start_ts": ws}]
        return paths

    def test_chronological_split_no_overlap(self) -> None:
        split = split_markets_chronological(self._paths(10))
        split.validate_no_overlap()
        self.assertEqual(
            len(split.train) + len(split.validation) + len(split.test),
            10,
        )


class BootstrapTest(unittest.TestCase):
    def test_deterministic_with_seed(self) -> None:
        trades = [_trade(f"m{i}", i * 10, pnl=0.05 + i * 0.01) for i in range(20)]
        a = bootstrap_market_metrics(trades, n_samples=200, seed=99)
        b = bootstrap_market_metrics(trades, n_samples=200, seed=99)
        self.assertEqual(a.ev_mean, b.ev_mean)
        self.assertEqual(a.ev_ci_low, b.ev_ci_low)


class CostModelTest(unittest.TestCase):
    def test_costs_never_improve_ideal_ev(self) -> None:
        trades = [_trade(f"m{i}", i, pnl=0.12) for i in range(30)]
        ideal = compute_stats(Strategy(direction="YES", max_entry=0.30, tp=0.60), trades)
        stressed = compute_stats(
            Strategy(direction="YES", max_entry=0.30, tp=0.60),
            apply_costs_same_trade_set(trades, BASE_COSTS),
        )
        self.assertLessEqual(stressed.expected_value, ideal.expected_value)


class ShadowExportTest(unittest.TestCase):
    def test_no_execution_imports(self) -> None:
        for mod_name in (
            "bot.research.strategy_simulator.finalists",
            "bot.research.strategy_simulator.storage",
            "bot.research.strategy_simulator.walk_forward",
        ):
            source = Path(importlib.import_module(mod_name).__file__).read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("execution", alias.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("execution", node.module)


class WalkForwardIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "wf.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed(self, conn: sqlite3.Connection, slug: str, ws: int, *, strike: float, final_btc: float) -> None:
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
        for i in range(25):
            ts = ws + i * 12
            sfs = i * 12
            sl = 300 - sfs
            btc = strike + (final_btc - strike) * (sfs / 288.0)
            yes_ask = min(0.95, 0.28 + sfs / 800.0)
            yes_bid = yes_ask - 0.02
            no_ask = 1.0 - yes_bid
            no_bid = no_ask - 0.02
            conn.execute(
                """
                INSERT INTO v4_shadow_observations (
                    market_slug, window_start_ts, timestamp, seconds_from_start,
                    seconds_left, btc_price, strike, delta,
                    yes_bid, yes_ask, no_bid, no_ask, spread
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug, ws, ts, sfs, sl, btc, strike, btc - strike,
                    yes_bid, yes_ask, no_bid, no_ask, 0.02,
                ),
            )

    def test_walk_forward_reproducible(self) -> None:
        with connect(self.db_path) as conn:
            for i in range(12):
                self._seed(
                    conn, f"btc-updown-5m-{i}", 1_700_000_000 + i * 400,
                    strike=100.0, final_btc=100.0 + (i % 3) - 1,
                )
            conn.commit()
            from bot.research.strategy_simulator.storage import ensure_tables
            ensure_tables(conn)
            split1, r1 = run_walk_forward(
                conn, min_obs=20, top_n=3, min_trades=1,
                show_progress=False, bootstrap_samples=100,
            )
            split2, r2 = run_walk_forward(
                conn, min_obs=20, top_n=3, min_trades=1,
                show_progress=False, bootstrap_samples=100,
            )
        self.assertEqual(split1.train, split2.train)
        self.assertEqual(len(r1), len(r2))
        if r1 and r2:
            self.assertEqual(r1[0].fingerprint, r2[0].fingerprint)


if __name__ == "__main__":
    unittest.main()
