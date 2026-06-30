"""Tests for Early Reversion strategy counters."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.er_stats import (
    fetch_ask_level_counts,
    fetch_strategy_counters,
    fetch_timing_counts,
    format_report,
    record_entry_executed,
    record_exit,
    record_strategy_check,
)


class ErStatsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_record_check_price_reached_and_ask_levels(self) -> None:
        with connect(self.db_path) as conn:
            record_strategy_check(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=12,
            )
            record_strategy_check(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.42,
                entry_threshold=0.40,
                seconds_open=45,
            )
            conn.commit()

            rows = fetch_strategy_counters(conn, "v2")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].checks, 2)
            self.assertEqual(rows[0].price_reached, 1)

            ask_levels = dict(fetch_ask_level_counts(conn, "v2", "NO_C"))
            self.assertEqual(ask_levels[0.50], 2)
            self.assertEqual(ask_levels[0.40], 1)
            self.assertNotIn(0.35, ask_levels)

            timing = fetch_timing_counts(conn, "v2", "NO_C")
            self.assertEqual(timing, [("0-30", 1)])

    def test_record_entry_and_exit(self) -> None:
        with connect(self.db_path) as conn:
            record_entry_executed(conn, "v2", "NO_C")
            record_exit(conn, "v2", "NO_C", "TRAILING_STOP")
            record_exit(conn, "v2", "NO_C", "STOP_LOSS")
            record_exit(conn, "v2", "NO_C", "TIME_STOP")
            conn.commit()

            row = fetch_strategy_counters(conn, "v2")[0]
            self.assertEqual(row.entries, 1)
            self.assertEqual(row.exits_tp, 1)
            self.assertEqual(row.exits_stop, 1)
            self.assertEqual(row.exits_expiration, 1)

    def test_format_report_contains_summary_and_histogram(self) -> None:
        with connect(self.db_path) as conn:
            record_strategy_check(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=55,
            )
            record_entry_executed(conn, "v2", "NO_C")
            conn.commit()

            report = format_report(conn, "v2")

        self.assertIn("NO_C", report)
        self.assertIn("Проверок", report)
        self.assertIn("ask <=0.40", report)
        self.assertIn("31-60", report)


if __name__ == "__main__":
    unittest.main()
