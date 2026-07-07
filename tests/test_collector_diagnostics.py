"""Tests for V4 collector diagnostics and observation density fixes."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.collector_diagnostics import analyze_last_markets, analyze_market_gaps, cmd_v4_density
from bot.database import connect, init_db, insert_v4_shadow_observation, insert_v4_shadow_trade
from bot.market_scanner import Btc5mMarket, TokenQuotes
from bot.v4.shadow_trade import process_v4_shadow, record_v4_observation_tick


def _make_market(*, window_start: int) -> Btc5mMarket:
    return Btc5mMarket(
        slug=f"btc-updown-5m-{window_start}",
        title="BTC 5m test",
        condition_id="cond-test",
        window_start_ts=window_start,
        end_ts=window_start + 300,
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_outcome="Up",
        no_outcome="Down",
        yes_quotes=TokenQuotes("yes-token", 0.55, 0.57),
        no_quotes=TokenQuotes("no-token", 0.38, 0.39),
    )


def _seed_observations(
    conn: sqlite3.Connection,
    *,
    window_start: int,
    count: int,
    gap_sec: int,
) -> None:
    slug = f"btc-updown-5m-{window_start}"
    for i in range(count):
        ts = window_start + i * gap_sec
        insert_v4_shadow_observation(
            conn,
            market_slug=slug,
            window_start_ts=window_start,
            timestamp=ts,
            seconds_from_start=min(299, i * gap_sec),
            seconds_left=max(0, 300 - i * gap_sec),
            btc_price=100_000.0,
            strike=100_000.0,
            delta=0.0,
            yes_bid=0.55,
            yes_ask=0.56,
            no_bid=0.38,
            no_ask=0.39,
            trend_score=None,
            trend_side=None,
            spread=0.01,
        )


class CollectorDiagnosticsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_analyze_market_gaps_10s_interval(self) -> None:
        window_start = 1_800_000_000
        with connect(self.db_path) as conn:
            _seed_observations(conn, window_start=window_start, count=30, gap_sec=10)
            conn.commit()
            path = conn.execute(
                "SELECT * FROM v4_shadow_observations WHERE market_slug = ? ORDER BY timestamp",
                (f"btc-updown-5m-{window_start}",),
            ).fetchall()
            rows = [dict(r) for r in path]
        stats = analyze_market_gaps(rows)
        assert stats is not None
        self.assertEqual(stats.obs_count, 30)
        self.assertEqual(stats.median_gap_sec, 10.0)
        self.assertAlmostEqual(300 / 10, stats.obs_count, delta=1)

    def test_v4_density_cli(self) -> None:
        import os
        from unittest import mock

        with connect(self.db_path) as conn:
            for i, ws in enumerate([1_800_000_000, 1_800_000_300, 1_800_000_600]):
                _seed_observations(
                    conn, window_start=ws, count=86 if i == 0 else 28, gap_sec=2 if i == 0 else 10
                )
            conn.commit()
        with mock.patch.dict(os.environ, {"DATABASE_PATH": str(self.db_path)}):
            rc = cmd_v4_density(["--last-markets", "3", "--min-obs", "5"])
        self.assertEqual(rc, 0)

    def test_observation_persisted_with_open_trade(self) -> None:
        window_start = int(time.time()) // 300 * 300
        market = _make_market(window_start=window_start)
        quotes = {"yes_bid": 0.55, "yes_ask": 0.56, "no_bid": 0.38, "no_ask": 0.39}

        with connect(self.db_path) as conn:
            insert_v4_shadow_trade(
                conn,
                market_slug=market.slug,
                window_start_ts=window_start,
                end_ts=window_start + 300,
                side="YES",
                entry_price=0.56,
                entry_ts=window_start + 60,
                entry_score=9.0,
                entry_probability=0.8,
                entry_reason="test",
            )
            conn.commit()

        with connect(self.db_path) as conn:
            inserted = process_v4_shadow(
                conn,
                market,
                quotes,
                btc_price=100_000.0,
                strike=100_000.0,
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM v4_shadow_observations WHERE market_slug = ?",
                (market.slug,),
            ).fetchone()[0]
        self.assertTrue(inserted)
        self.assertEqual(count, 1)

    def test_record_tick_dedupes_same_second(self) -> None:
        window_start = 1_800_000_100
        market = _make_market(window_start=window_start)
        quotes = {"yes_bid": 0.55, "yes_ask": 0.56, "no_bid": 0.38, "no_ask": 0.39}
        fixed_ts = window_start + 10

        with connect(self.db_path) as conn:
            first = record_v4_observation_tick(
                conn,
                market,
                quotes,
                btc_price=100_000.0,
                strike=100_000.0,
                now_ts=fixed_ts,
            )
            second = record_v4_observation_tick(
                conn,
                market,
                quotes,
                btc_price=100_001.0,
                strike=100_000.0,
                now_ts=fixed_ts,
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM v4_shadow_observations",
            ).fetchone()[0]
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(count, 1)

    def test_analyze_last_markets_ordering(self) -> None:
        with connect(self.db_path) as conn:
            _seed_observations(conn, window_start=1_800_000_100, count=10, gap_sec=2)
            _seed_observations(conn, window_start=1_800_000_500, count=10, gap_sec=2)
            conn.commit()
            stats = analyze_last_markets(conn, last_n=1, min_obs=5)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0].window_start_ts, 1_800_000_500)


if __name__ == "__main__":
    unittest.main()
