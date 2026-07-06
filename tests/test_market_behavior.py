"""Tests for observe-only market behavior research."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.market_behavior.analyzer import (
    aggregate_tp_samples,
    analyze_late_windows,
    analyze_market_summary,
    collect_tp_samples,
    entry_price_bucket,
)
from bot.research.market_behavior.engine import run_analysis
from bot.research.market_behavior.report import render_report
from bot.research.market_behavior.schema import (
    LATE_WINDOW_TABLE,
    SUMMARY_TABLE,
    TP_PROBABILITY_TABLE,
    ensure_tables,
)


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
    # Build 25 observations across the 5m window
    for i in range(25):
        ts = ws + i * 12
        sfs = i * 12
        sl = 300 - sfs
        # BTC trends up: YES should win when final_btc > strike
        btc = strike + (final_btc - strike) * (sfs / 288.0)
        yes_ask = min(0.95, 0.35 + sfs / 600.0)
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


class MarketBehaviorAnalyzerTest(unittest.TestCase):
    def test_entry_price_bucket(self) -> None:
        label, mid = entry_price_bucket(0.47)
        self.assertEqual(label, "45-50c")
        self.assertAlmostEqual(mid, 0.475)

    def test_winning_side_yes(self) -> None:
        obs = [
            {"timestamp": 1, "seconds_from_start": 0, "seconds_left": 300,
             "btc_price": 100.0, "strike": 99.0, "yes_bid": 0.4, "yes_ask": 0.42,
             "no_bid": 0.58, "no_ask": 0.6, "window_start_ts": 0},
            {"timestamp": 290, "seconds_from_start": 290, "seconds_left": 10,
             "btc_price": 101.0, "strike": 99.0, "yes_bid": 0.7, "yes_ask": 0.72,
             "no_bid": 0.28, "no_ask": 0.3, "window_start_ts": 0},
        ]
        summary = analyze_market_summary("btc-updown-5m-test", obs)
        self.assertEqual(summary.winning_side, "YES")
        self.assertAlmostEqual(summary.btc_delta_final, 2.0)

    def test_late_window_buckets(self) -> None:
        obs = []
        ws = 1_000
        for sl in range(300, 0, -12):
            obs.append({
                "timestamp": ws + (300 - sl),
                "seconds_from_start": 300 - sl,
                "seconds_left": sl,
                "btc_price": 100.0 + (300 - sl) * 0.01,
                "strike": 100.0,
                "yes_bid": 0.4, "yes_ask": 0.42,
                "no_bid": 0.58, "no_ask": 0.6,
                "window_start_ts": ws,
            })
        late = analyze_late_windows("m", obs)
        self.assertEqual(len(late), 5)
        by_bucket = {lw.seconds_bucket: lw for lw in late}
        self.assertIsNotNone(by_bucket[60].btc_delta_vs_strike)
        self.assertIsNotNone(by_bucket[5].btc_move_to_close)

    def test_tp_samples_forward_reach(self) -> None:
        obs = [
            {"timestamp": 1, "seconds_left": 120, "yes_ask": 0.40, "yes_bid": 0.38,
             "no_ask": 0.62, "no_bid": 0.60},
            {"timestamp": 2, "seconds_left": 60, "yes_ask": 0.55, "yes_bid": 0.53,
             "no_ask": 0.47, "no_bid": 0.45},
            {"timestamp": 3, "seconds_left": 10, "yes_ask": 0.70, "yes_bid": 0.68,
             "no_ask": 0.32, "no_bid": 0.30},
        ]
        samples = collect_tp_samples(obs)
        yes_reach_55 = [
            s for s in samples
            if s.side == "YES" and s.tp_level == 0.55 and s.entry_bucket == "40-45c"
        ]
        self.assertTrue(any(s.reached for s in yes_reach_55))


class MarketBehaviorEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "mb.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_run_analysis_persists_mb_tables(self) -> None:
        with connect(self.db_path) as conn:
            _seed_market(conn, "btc-updown-5m-a", strike=100.0, final_btc=102.0)
            _seed_market(conn, "btc-updown-5m-b", strike=100.0, final_btc=98.0)
            conn.commit()
            ensure_tables(conn)
            conn.commit()
            report = run_analysis(conn, min_obs=20, persist=True)
            n_summary = conn.execute(f"SELECT COUNT(*) AS n FROM {SUMMARY_TABLE}").fetchone()["n"]
            n_late = conn.execute(f"SELECT COUNT(*) AS n FROM {LATE_WINDOW_TABLE}").fetchone()["n"]
            n_tp = conn.execute(f"SELECT COUNT(*) AS n FROM {TP_PROBABILITY_TABLE}").fetchone()["n"]

        self.assertEqual(report.markets_analyzed, 2)
        self.assertEqual(n_summary, 2)
        self.assertEqual(n_late, 10)
        self.assertGreater(n_tp, 0)
        text = render_report(report)
        self.assertIn("MARKET BEHAVIOR RESEARCH REPORT", text)
        self.assertIn("YES wins", text)

    def test_does_not_modify_v4_table(self) -> None:
        with connect(self.db_path) as conn:
            _seed_market(conn, "btc-updown-5m-a", strike=100.0, final_btc=101.0)
            conn.commit()
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM v4_shadow_observations",
            ).fetchone()["n"]
            run_analysis(conn, min_obs=20, persist=True)
            after = conn.execute(
                "SELECT COUNT(*) AS n FROM v4_shadow_observations",
            ).fetchone()["n"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
