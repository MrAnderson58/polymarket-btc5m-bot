"""Tests for 1h and daily independent research pipelines."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.database import connect, init_db
from bot.research.mtf.research_1h.engine import run_1h_research
from bot.research.mtf.research_1h.families import family_early_move_1m
from bot.research.mtf.research_1h.models import Obs1h, parse_1h_window
from bot.research.mtf.research_daily.engine import run_daily_research
from bot.research.mtf.research_daily.models import ObsDaily, parse_daily_window, session_label
from bot.research.mtf.snapshots import ensure_tables, insert_snapshot

ET = ZoneInfo("America/New_York")


def _seed_1h(conn, ws: int, n: int = 15) -> str:
    slug = f"bitcoin-up-or-down-july-6-2026-1pm-et"
    # use ws from parse if needed - for test use fixed slug
    for i in range(n):
        ts = ws + 120 + i * 120
        if ts >= ws + 3600:
            break
        insert_snapshot(conn, {
            "timestamp": ts,
            "btc_price": 60000 + i * 20,
            "market_1h_slug": slug,
            "market_1h_yes_bid": 0.48,
            "market_1h_yes_ask": 0.50,
            "market_1h_no_bid": 0.50,
            "market_1h_no_ask": 0.52,
            "market_1h_strike": 60000,
            "market_1h_seconds_left": ws + 3600 - ts,
        })
    return slug


def _seed_daily(conn, ws: int, n: int = 15) -> str:
    slug = "bitcoin-up-or-down-on-july-6-2026"
    we = ws + 86400
    for i in range(n):
        ts = ws + 3600 + i * 3600
        if ts >= we:
            break
        insert_snapshot(conn, {
            "timestamp": ts,
            "btc_price": 60000 + i * 50,
            "market_daily_slug": slug,
            "market_daily_yes_bid": 0.47,
            "market_daily_yes_ask": 0.49,
            "market_daily_no_bid": 0.51,
            "market_daily_no_ask": 0.53,
            "market_daily_strike": 59500,
            "market_daily_seconds_left": we - ts,
        })
    return slug


class Research1hTestCase(unittest.TestCase):
    def test_parse_1h_window(self) -> None:
        ws = int(datetime(2026, 7, 6, 13, 0, tzinfo=ET).timestamp())
        bounds = parse_1h_window("bitcoin-up-or-down-july-6-2026-1pm-et")
        self.assertIsNotNone(bounds)
        self.assertEqual(bounds[0], ws)
        self.assertEqual(bounds[1], ws + 3600)

    def test_early_move_family(self) -> None:
        obs = Obs1h(
            timestamp=100, market_slug="s", window_start_ts=0, window_end_ts=3600,
            entry_second=100, seconds_left=3500, btc_price=60100, strike=60000,
            yes_bid=0.5, yes_ask=0.52, no_bid=0.48, no_ask=0.5, spread=0.02, yes_mid=0.51,
            btc_move_1m=50,
        )
        side = family_early_move_1m(obs, [obs], 0, {"move_1m_usd": 30})
        self.assertEqual(side, "YES")


class ResearchDailyTestCase(unittest.TestCase):
    def test_parse_daily_window(self) -> None:
        bounds = parse_daily_window("bitcoin-up-or-down-on-july-6-2026")
        self.assertIsNotNone(bounds)
        self.assertEqual(bounds[1] - bounds[0], 86400)

    def test_session_label(self) -> None:
        self.assertIn(session_label(3600 * 2), ("asia", "europe", "us"))


class ResearchEngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_1h_insufficient_data(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            result = run_1h_research(conn)
        self.assertEqual(result["verdict"], "COLLECT_MORE_DATA")

    def test_daily_insufficient_data(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            result = run_daily_research(conn)
        self.assertEqual(result["verdict"], "COLLECT_MORE_DATA")

    def test_1h_runs_with_markets(self) -> None:
        ws = int(datetime(2026, 7, 6, 13, 0, tzinfo=ET).timestamp())
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            for k in range(18):
                _seed_1h(conn, ws + k * 3600)
            conn.commit()
            result = run_1h_research(conn)
        self.assertIn(result["verdict"], (
            "NO_EDGE", "COLLECT_MORE_DATA", "CONTINUE_RESEARCH", "READY_FOR_1H_SHADOW",
        ))

    def test_daily_runs_with_markets(self) -> None:
        ws = int(datetime(2026, 7, 5, 12, 0, tzinfo=ET).timestamp())
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            for k in range(12):
                _seed_daily(conn, ws + k * 86400)
            conn.commit()
            result = run_daily_research(conn)
        self.assertIn(result["verdict"], (
            "NO_EDGE", "COLLECT_MORE_DATA", "CONTINUE_RESEARCH", "READY_FOR_DAILY_SHADOW",
        ))


if __name__ == "__main__":
    unittest.main()
