"""Regression tests for audit-production bounded runtime."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.database import connect, init_db
from bot.research.mtf.integrity_audit import audit_production
from bot.research.mtf.snapshots import ensure_tables, insert_snapshot


def _slow_discover(*_args, **_kwargs):
    time.sleep(60)
    return None


class AuditProductionBoundedRuntimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_rows(self, conn: sqlite3.Connection, n: int) -> None:
        ws = 1_720_000_000
        slug_1h = "bitcoin-up-or-down-july-3-2024-1pm-et"
        slug_daily = "bitcoin-up-or-down-on-july-4-2024"
        slug_15m = f"btc-updown-15m-{ws}"
        for i in range(n):
            ts = ws + i * 2
            insert_snapshot(conn, {
                "timestamp": ts,
                "btc_price": 60000 + i,
                "market_15m_slug": slug_15m,
                "market_15m_yes_bid": 0.48,
                "market_15m_yes_ask": 0.50,
                "market_15m_no_bid": 0.50,
                "market_15m_no_ask": 0.52,
                "market_15m_strike": 60000,
                "market_15m_seconds_left": 900 - (i * 2),
                "market_1h_slug": slug_1h,
                "market_1h_yes_bid": 0.47,
                "market_1h_yes_ask": 0.49,
                "market_1h_strike": 60000,
                "market_1h_seconds_left": 3600,
                "market_daily_slug": slug_daily,
                "market_daily_yes_bid": 0.46,
                "market_daily_yes_ask": 0.48,
                "market_daily_strike": 59500,
                "market_daily_seconds_left": 86400,
            })
        conn.commit()

    def test_audit_completes_quickly_with_many_rows(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            self._seed_rows(conn, 500)
            started = time.monotonic()
            report = audit_production(conn, offline=True, progress=False)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0, f"audit took {elapsed:.1f}s — likely HTTP in alignment path")
        self.assertEqual(report["snapshots"]["total"], 500)
        self.assertEqual(report["mode"], "offline")

    @patch("bot.research.mtf.discovery.discover_1h_market", side_effect=_slow_discover)
    @patch("bot.research.mtf.discovery.discover_daily_market", side_effect=_slow_discover)
    def test_audit_ignores_slow_discovery_http(
        self, _mock_daily, _mock_1h,
    ) -> None:
        """audit-production must not call Gamma discovery per snapshot row."""
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            self._seed_rows(conn, 200)
            started = time.monotonic()
            report = audit_production(conn, offline=True, progress=False)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 3.0)
        self.assertGreater(report["1h"]["slug_coverage"], 0)


if __name__ == "__main__":
    unittest.main()
