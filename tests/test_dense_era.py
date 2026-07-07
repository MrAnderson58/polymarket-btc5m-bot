"""Tests for dense-era boundary and market filters."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import init_db, insert_v4_shadow_observation
from bot.research.strategy_simulator.dense_era import (
    assess_completed_market,
    detect_dense_era_boundary,
)
from bot.research.strategy_simulator.market_filter import MarketFilter, market_passes_filter


def _seed_market(
    conn: sqlite3.Connection,
    *,
    window_start: int,
    count: int,
    gap: int,
    span_ok: bool = True,
) -> None:
    slug = f"btc-updown-5m-{window_start}"
    for i in range(count):
        ts = window_start + i * gap
        sl = 300 - i * gap if span_ok else 200
        insert_v4_shadow_observation(
            conn,
            market_slug=slug,
            window_start_ts=window_start,
            timestamp=ts,
            seconds_from_start=i * gap,
            seconds_left=max(0, sl),
            btc_price=100_000.0,
            strike=100_000.0,
            delta=0.0,
            yes_bid=0.32,
            yes_ask=0.33,
            no_bid=0.66,
            no_ask=0.67,
            trend_score=None,
            trend_side=None,
            spread=0.01,
        )


class DenseEraTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_dense_market_passes(self) -> None:
        ws = 1_900_000_000
        path = []
        for i in range(90):
            path.append({
                "market_slug": f"btc-updown-5m-{ws}",
                "window_start_ts": ws,
                "timestamp": ws + i * 3,
                "seconds_from_start": i * 3,
                "seconds_left": max(0, 300 - i * 3),
                "yes_bid": 0.32,
                "yes_ask": 0.33,
            })
        q = assess_completed_market(path, min_obs=60, max_median_gap=5.0)
        self.assertIsNotNone(q)
        assert q is not None
        self.assertTrue(q.passes_dense)

    def test_boundary_detection(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # sparse era
            for j, ws in enumerate(range(1_900_000_000, 1_900_000_000 + 5 * 300, 300)):
                _seed_market(conn, window_start=ws, count=28, gap=10, span_ok=True)
            # dense era
            dense_start = 1_900_002_000
            for k in range(6):
                _seed_market(
                    conn,
                    window_start=dense_start + k * 300,
                    count=90,
                    gap=3,
                    span_ok=True,
                )
            conn.commit()
            boundary = detect_dense_era_boundary(conn, consecutive_required=5)
        self.assertIsNotNone(boundary.boundary_window_start_ts)

    def test_market_filter_by_gap(self) -> None:
        ws = 1_900_000_500
        path = []
        for i in range(30):
            path.append({
                "market_slug": f"btc-updown-5m-{ws}",
                "window_start_ts": ws,
                "timestamp": ws + i * 10,
                "seconds_from_start": i * 10,
                "seconds_left": max(0, 300 - i * 10),
                "yes_bid": 0.32,
                "yes_ask": 0.33,
            })
        filt = MarketFilter(max_median_gap=5.0, min_obs_per_market=20, completed_only=True)
        self.assertFalse(market_passes_filter(path, filt))


if __name__ == "__main__":
    unittest.main()
