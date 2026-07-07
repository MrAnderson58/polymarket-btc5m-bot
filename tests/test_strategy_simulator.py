"""Tests for observe-only strategy simulator."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from statistics import mean

from bot.database import connect, init_db
from bot.research.strategy_simulator.engine import run_discovery, run_simulation
from bot.research.strategy_simulator.grid import generate_discovery_grid
from bot.research.strategy_simulator.features import build_snapshot_features
from bot.research.strategy_simulator.report import render_discovery_report, render_simulation_report, validate_simulation
from bot.research.strategy_simulator.simulator import simulate_strategy_on_market
from bot.research.strategy_simulator.statistics import compute_stats
from bot.research.strategy_simulator.storage import ensure_tables
from bot.research.strategy_simulator.strategies import Strategy


def _seed_market(conn: sqlite3.Connection, slug: str, *, strike: float, final_btc: float) -> None:
    ws = 1_700_000_000
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS v4_shadow_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            window_start_ts INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            seconds_from_start INTEGER NOT NULL,
            seconds_left INTEGER NOT NULL,
            btc_price REAL NOT NULL,
            strike REAL,
            delta REAL,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            trend_score REAL,
            trend_side TEXT,
            spread REAL,
            created_at TEXT
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


def _make_path(*, strike: float = 100.0, yes_ask_start: float = 0.28) -> list[dict]:
    ws = 1_000
    obs = []
    for i in range(20):
        ts = ws + i * 10
        sl = 200 - i * 10
        btc = strike + i * 0.5
        yes_ask = min(0.95, yes_ask_start + i * 0.02)
        yes_bid = yes_ask - 0.02
        obs.append({
            "timestamp": ts,
            "seconds_left": sl,
            "seconds_from_start": i * 10,
            "btc_price": btc,
            "strike": strike,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": 1.0 - yes_ask,
            "no_ask": 1.0 - yes_bid,
            "window_start_ts": ws,
        })
    return obs


class StrategySimulatorFeaturesTest(unittest.TestCase):
    def test_btc_velocity_and_spread_dynamics(self) -> None:
        path = _make_path()
        feat = build_snapshot_features(path, len(path) - 1, side="YES")
        assert feat is not None
        self.assertIsNotNone(feat.btc_velocity_5s)
        self.assertIsNotNone(feat.delta_5s)
        self.assertIsNotNone(feat.spread_now)
        self.assertIsNotNone(feat.spread_5s_ago)
        self.assertIsNotNone(feat.spread_change)

    def test_features_no_lookahead(self) -> None:
        path = _make_path()
        idx = 5
        feat = build_snapshot_features(path, idx, side="YES")
        assert feat is not None
        path[idx + 1]["btc_price"] = 999.0
        path[idx + 1]["yes_ask"] = 0.99
        feat2 = build_snapshot_features(path, idx, side="YES")
        assert feat2 is not None
        self.assertEqual(feat.btc_price, feat2.btc_price)
        self.assertEqual(feat.yes_ask, feat2.yes_ask)


class StrategySimulatorReplayTest(unittest.TestCase):
    def test_forward_replay_tp_hit(self) -> None:
        path = _make_path(yes_ask_start=0.28)
        strategy = Strategy(
            direction="YES",
            max_entry=0.30,
            min_delta=0,
            max_spread=0.03,
            min_seconds_left=60,
            tp=0.60,
        )
        trades = simulate_strategy_on_market("m", path, strategy, one_trade_per_market=True)
        self.assertTrue(trades)
        self.assertTrue(trades[0].won)
        self.assertAlmostEqual(trades[0].pnl, 0.60 - trades[0].entry_price, places=4)

    def test_no_lookahead_entry_uses_only_past(self) -> None:
        path = _make_path(yes_ask_start=0.28)
        strategy = Strategy(
            direction="YES",
            max_entry=0.30,
            min_delta=None,
            max_spread=0.03,
            min_seconds_left=60,
            tp=0.60,
        )
        trades_before = simulate_strategy_on_market("m", path, strategy)
        future = path[-1]
        future["yes_bid"] = 0.05
        future["yes_ask"] = 0.07
        trades_after = simulate_strategy_on_market("m", path, strategy)
        self.assertEqual(len(trades_before), len(trades_after))
        for a, b in zip(trades_before, trades_after):
            self.assertEqual(a.entry_ts, b.entry_ts)
            self.assertEqual(a.entry_price, b.entry_price)

    def test_ev_matches_pnl_mean(self) -> None:
        path = _make_path(yes_ask_start=0.28)
        strategy = Strategy(
            direction="YES",
            max_entry=0.35,
            min_delta=None,
            max_spread=0.05,
            min_seconds_left=30,
            tp=0.55,
        )
        trades = simulate_strategy_on_market("m", path, strategy)
        stats = compute_stats(strategy, trades)
        if trades:
            self.assertAlmostEqual(stats.expected_value, mean(t.pnl for t in trades), places=9)

    def test_reproducible_results(self) -> None:
        path = _make_path(yes_ask_start=0.25)
        strategy = Strategy(
            direction="YES",
            max_entry=0.30,
            min_delta=0,
            max_spread=0.03,
            min_seconds_left=60,
            tp=0.60,
        )
        t1 = simulate_strategy_on_market("m", path, strategy)
        t2 = simulate_strategy_on_market("m", path, strategy)
        self.assertEqual(len(t1), len(t2))
        for a, b in zip(t1, t2):
            self.assertEqual(a.pnl, b.pnl)
            self.assertEqual(a.won, b.won)


class StrategySimulatorEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "ss.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_simulate_cli_persists(self) -> None:
        strategy = Strategy(
            direction="YES",
            max_entry=0.35,
            min_delta=0,
            max_spread=0.03,
            min_seconds_left=60,
            tp=0.60,
        )
        with connect(self.db_path) as conn:
            _seed_market(conn, "btc-updown-5m-a", strike=100.0, final_btc=105.0)
            _seed_market(conn, "btc-updown-5m-b", strike=100.0, final_btc=95.0)
            conn.commit()
            ensure_tables(conn)
            conn.commit()
            stats, trades = run_simulation(
                conn, strategy, min_obs=20, one_trade_per_market=True, persist=True,
            )
            n = conn.execute("SELECT COUNT(*) AS n FROM ss_simulation_results").fetchone()["n"]
        self.assertEqual(n, 1)
        text = render_simulation_report(stats, trades, min_trades=1)
        self.assertIn("SIMULATION", text)
        self.assertIn("Trades", text)

    def test_discovery_grid_and_report(self) -> None:
        grid = generate_discovery_grid()
        self.assertGreater(len(grid), 1000)
        with connect(self.db_path) as conn:
            _seed_market(conn, "btc-updown-5m-a", strike=100.0, final_btc=105.0)
            _seed_market(conn, "btc-updown-5m-b", strike=100.0, final_btc=95.0)
            conn.commit()
            ensure_tables(conn)
            conn.commit()
            ranked, _families = run_discovery(
                conn, min_obs=20, min_trades=1, top_n=5, persist=True,
            )
        text = render_discovery_report(ranked, min_trades=1)
        self.assertIn("TOP HISTORICAL STRATEGIES", text)

    def test_train_test_non_overlap_placeholder(self) -> None:
        """Placeholder for future train/test split validation."""
        train = {"btc-updown-5m-a", "btc-updown-5m-b"}
        test = {"btc-updown-5m-c", "btc-updown-5m-d"}
        self.assertFalse(train & test)


class StrategySimulatorQcTest(unittest.TestCase):
    def test_qc_rejects_low_sample(self) -> None:
        strategy = Strategy(direction="YES", max_entry=0.30, tp=0.60)
        stats = compute_stats(strategy, [])
        qc = validate_simulation(stats, [], min_trades=30)
        self.assertFalse(qc.ok)


class PathIndexTest(unittest.TestCase):
    def test_bisect_matches_linear_scan(self) -> None:
        from bot.research.strategy_simulator.path_index import find_idx_at_or_before

        timestamps = [100, 108, 116, 124, 132, 140, 148, 156]
        for idx in range(len(timestamps)):
            for target in (100, 110, 116, 130, 156, 200):
                pos = find_idx_at_or_before(timestamps, idx, target)
                best = None
                for i in range(idx, -1, -1):
                    ts = timestamps[i]
                    if ts <= target:
                        best = i
                    if ts < target - 5:
                        break
                self.assertEqual(pos, best)

    def test_context_matches_direct_simulation(self) -> None:
        from bot.research.strategy_simulator.simulator import build_market_context, simulate_strategy_on_context

        path = _make_path(yes_ask_start=0.28)
        strategy = Strategy(
            direction="YES",
            max_entry=0.30,
            min_delta=0,
            max_spread=0.03,
            min_seconds_left=60,
            tp=0.60,
        )
        ctx = build_market_context("m", path)
        direct = simulate_strategy_on_market("m", path, strategy, one_trade_per_market=True)
        cached = simulate_strategy_on_context(ctx, strategy, one_trade_per_market=True)
        self.assertEqual(len(direct), len(cached))
        for a, b in zip(direct, cached):
            self.assertAlmostEqual(a.pnl, b.pnl)
            self.assertEqual(a.won, b.won)


if __name__ == "__main__":
    unittest.main()
