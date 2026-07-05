"""Tests for independent 15m bidirectional research."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.mtf.discovery import parse_15m_window_start_ts
from bot.research.mtf.research_15m.engine import run_15m_research
from bot.research.mtf.research_15m.exits import simulate_exit, try_entry
from bot.research.mtf.research_15m.families import (
    FAMILIES,
    calibrate_thresholds,
    family_early_momentum,
)
from bot.research.mtf.research_15m.metrics import pf, summarize_trades
from bot.research.mtf.research_15m.models import Obs15m, SimTrade
from bot.research.mtf.research_15m.observations import load_15m_market_paths
from bot.research.mtf.snapshots import ensure_tables, insert_snapshot


def _seed_15m_path(conn: sqlite3.Connection, ws: int, n: int = 20) -> str:
    slug = f"btc-updown-15m-{ws}"
    strike = 60000.0
    for i in range(n):
        ts = ws + 30 + i * 30
        if ts >= ws + 900:
            break
        btc = strike + (i * 8)
        ya = 0.45 + i * 0.01
        insert_snapshot(conn, {
            "timestamp": ts,
            "btc_price": btc,
            "market_5m_slug": f"btc-updown-5m-{ws}",
            "market_15m_slug": slug,
            "market_15m_yes_bid": ya - 0.01,
            "market_15m_yes_ask": ya,
            "market_15m_no_bid": 1 - ya - 0.01,
            "market_15m_no_ask": 1 - ya,
            "market_15m_strike": strike,
            "market_15m_seconds_left": ws + 900 - ts,
        })
    return slug


class Research15mFamiliesTestCase(unittest.TestCase):
    def test_early_momentum_yes(self) -> None:
        obs = Obs15m(
            timestamp=100, market_slug="s", window_start_ts=0, entry_second=100,
            seconds_left=800, btc_price=60100, strike=60000,
            yes_bid=0.5, yes_ask=0.52, no_bid=0.48, no_ask=0.5, spread=0.02,
            btc_move_1m=50, momentum_consistency=1.0, yes_mid=0.51,
        )
        side = family_early_momentum(obs, [obs], 0, {"early_1m_usd": 30})
        self.assertEqual(side, "YES")

    def test_calibrate_from_train(self) -> None:
        obs = [Obs15m(
            timestamp=i, market_slug="s", window_start_ts=0, entry_second=i,
            seconds_left=900 - i, btc_price=60000, strike=60000,
            yes_bid=0.5, yes_ask=0.52, no_bid=0.48, no_ask=0.5, spread=0.02,
            btc_move_1m=float(i), yes_mid=0.51,
        ) for i in range(10, 100, 10)]
        th = calibrate_thresholds(obs)
        self.assertIn("early_1m_usd", th)


class Research15mExitTestCase(unittest.TestCase):
    def test_settlement_exit(self) -> None:
        ws = 1_700_000_000
        path = [
            Obs15m(ws + 60, f"btc-updown-15m-{ws}", ws, 60, 840, 60100, 60000, 0.5, 0.52, 0.48, 0.5, 0.02, yes_mid=0.51),
            Obs15m(ws + 890, f"btc-updown-15m-{ws}", ws, 890, 10, 60200, 60000, 0.9, 0.91, 0.09, 0.1, 0.01, yes_mid=0.9),
        ]
        trade = try_entry(path, 0, "YES")
        self.assertIsNotNone(trade)
        closed = simulate_exit(trade, path, 0, "settlement")
        self.assertIsNotNone(closed.pnl_pct)
        self.assertEqual(closed.exit_reason, "settlement")


class Research15mEngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_load_paths_no_look_ahead_window(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            ws = 1_700_000_000
            _seed_15m_path(conn, ws)
            conn.commit()
            paths = load_15m_market_paths(conn)
        self.assertEqual(len(paths), 1)
        slug = f"btc-updown-15m-{ws}"
        self.assertIn(slug, paths)
        self.assertEqual(parse_15m_window_start_ts(slug), ws)

    def test_research_insufficient_data(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            result = run_15m_research(conn)
        self.assertEqual(result["verdict"], "COLLECT_MORE_DATA")

    def test_research_runs_with_many_markets(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            for k in range(25):
                _seed_15m_path(conn, 1_700_000_000 + k * 900, n=15)
            conn.commit()
            result = run_15m_research(conn)
        self.assertIn(result["verdict"], (
            "NO_EDGE", "COLLECT_MORE_DATA", "CONTINUE_RESEARCH", "READY_FOR_15M_SHADOW",
        ))
        self.assertGreater(result["data_audit"]["markets"], 0)


class Research15mMetricsTestCase(unittest.TestCase):
    def test_pf_and_summary(self) -> None:
        trades = [
            SimTrade("f", "YES", "s", 0, 0.5, 0, pnl_pct=10.0),
            SimTrade("f", "YES", "s", 0, 0.5, 0, pnl_pct=-5.0),
        ]
        s = summarize_trades(trades)
        self.assertEqual(s["n"], 2)
        self.assertAlmostEqual(pf([10, -5]), 2.0)


if __name__ == "__main__":
    unittest.main()
