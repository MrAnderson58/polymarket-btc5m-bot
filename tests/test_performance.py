"""Tests for shock paper performance analytics."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN
from bot.research.market_events.performance import (
    compute_performance,
    export_performance_csv,
    format_equity_curve_ascii,
    format_performance_report,
    load_completed_trades,
)


class TestPerformance(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "perf.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_closed_runs(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO market_events (
                  event_ts, detected_ts, venue, symbol, direction, phase,
                  trigger_window_seconds, return_pct, classification,
                  detector_version, detector_triggers_json, dedup_key, created_at
                ) VALUES (?, ?, 'bybit', 'SOL', ?, 'SHOCK_DETECTED',
                  60, -2.0, 'ASSET_SPECIFIC', 'v1', '["SHOCK_A"]', ?, ?)
                """,
                (now - 100, now - 100, SHOCK_DIRECTION_DOWN, f"dedup-perf-{now}", now - 100),
            )
            eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            for i, net in enumerate((1.2, -0.8, 2.0, -1.0)):
                conn.execute(
                    """
                    INSERT INTO paper_strategy_runs (
                      event_id, strategy_name, strategy_version, reversal_variant,
                      exit_variant, eligibility, entry_ts, entry_price, exit_ts,
                      exit_price, gross_return, net_return, fee_bps, slippage_bps,
                      duration_seconds, created_at
                    ) VALUES (?, ?, 'v1', 'R1', 'EXIT_A', 1, ?, 100.0, ?, 101.0,
                              ?, ?, 10, 10, 60, ?)
                    """,
                    (
                        eid,
                        f"REVERSAL_R1_EXIT_{chr(65+i)}",
                        now - 200 + i,
                        now - 100 + i,
                        net + 0.4,
                        net,
                        now,
                    ),
                )
            conn.commit()

    def test_metrics_and_streaks(self) -> None:
        self._seed_closed_runs()
        with market_events_connection() as conn:
            trades = load_completed_trades(conn, notional=100.0)
            self.assertEqual(len(trades), 4)
            stats = compute_performance(trades, starting_equity=1000.0)
        self.assertEqual(stats.trades, 4)
        self.assertEqual(stats.wins, 2)
        self.assertEqual(stats.losses, 2)
        self.assertAlmostEqual(stats.net_profit_usd, 100 * (1.2 - 0.8 + 2.0 - 1.0) / 100)
        self.assertEqual(stats.longest_win_streak, 1)
        self.assertGreater(stats.max_drawdown_usd, 0)

    def test_strategy_breakdown(self) -> None:
        self._seed_closed_runs()
        with market_events_connection() as conn:
            trades = load_completed_trades(conn)
            stats = compute_performance(trades)
        self.assertEqual(len(stats.by_strategy), 4)

    def test_report_equity_csv(self) -> None:
        self._seed_closed_runs()
        with market_events_connection() as conn:
            trades = load_completed_trades(conn)
            stats = compute_performance(trades, starting_equity=1000.0)
            report = format_performance_report(stats)
            curve = format_equity_curve_ascii(stats)
            csv_path = Path(self.tmp.name) / "out.csv"
            n = export_performance_csv(trades, csv_path)
        self.assertIn("PERFORMANCE REPORT", report)
        self.assertIn("Profit Factor", report)
        self.assertIn("Equity", curve)
        self.assertEqual(n, 4)
        self.assertTrue(csv_path.read_text().startswith("timestamp"))

    def test_cli_registered(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertIn("performance", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
