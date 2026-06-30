"""Tests for trailing stop activation statistics."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import (
    close_early_reversion_v2_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
    update_early_reversion_v2_trade_tracking,
)
from bot.er_trailing_stats import fetch_trailing_strategy_stats, format_trailing_summary


class ErTrailingStatsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {"ENABLED_STRATEGIES": "YES_C,NO_C"},
            clear=False,
        )
        self._env_patch.start()
        import importlib
        import bot.config as config
        import bot.er_trailing_stats as er_trailing_stats

        importlib.reload(config)
        importlib.reload(er_trailing_stats)
        self.er_trailing_stats = er_trailing_stats

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _insert_closed_trade(
        self,
        conn,
        *,
        slug: str,
        entry_price: float = 0.40,
        max_price_seen: float = 0.43,
        trailing_active: bool = False,
        trailing_activation_price: float | None = None,
        highest_price: float | None = None,
        exit_price: float = 0.42,
        exit_reason: str = "TRAILING_STOP",
    ) -> int:
        now = int(time.time())
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug=slug,
            window_start_ts=now,
            end_ts=now + 300,
            side="NO",
            strategy_name="NO_C",
            entry_price=entry_price,
            entry_ts=now,
        )
        update_early_reversion_v2_trade_tracking(
            conn,
            trade_id,
            max_price_seen=max_price_seen,
            last_bid=max_price_seen,
            trailing_active=trailing_active,
            trailing_activation_price=trailing_activation_price,
            highest_price=highest_price,
        )
        close_early_reversion_v2_trade(
            conn,
            trade_id,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl_percent=5.0,
            pnl_usdc=0.10,
            holding_time_seconds=30.0,
        )
        return trade_id

    def test_trailing_stats_counts_activation_and_exits(self) -> None:
        with connect(self.db_path) as conn:
            self._insert_closed_trade(
                conn,
                slug="btc-updown-5m-trail",
                max_price_seen=0.44,
                trailing_active=True,
                trailing_activation_price=0.43,
                highest_price=0.44,
                exit_reason="TRAILING_STOP",
            )
            self._insert_closed_trade(
                conn,
                slug="btc-updown-5m-stop",
                max_price_seen=0.41,
                trailing_active=False,
                exit_price=0.36,
                exit_reason="STOP_LOSS",
            )
            conn.commit()
            stats = fetch_trailing_strategy_stats(conn, "NO_C")

        self.assertEqual(stats.entries, 2)
        self.assertEqual(stats.trailing_activated, 1)
        self.assertAlmostEqual(stats.activation_rate, 0.5)
        self.assertEqual(stats.exited_by_trailing, 1)
        self.assertEqual(stats.exited_by_stop_loss, 1)
        self.assertEqual(stats.warnings, ())

    def test_legacy_trailing_stop_not_counted_as_activated_exit(self) -> None:
        with connect(self.db_path) as conn:
            self._insert_closed_trade(
                conn,
                slug="btc-updown-5m-legacy-trail",
                max_price_seen=0.405,
                exit_reason="TRAILING_STOP",
            )
            conn.commit()
            stats = fetch_trailing_strategy_stats(conn, "NO_C")

        self.assertEqual(stats.exited_by_trailing, 0)
        self.assertEqual(stats.exited_by_trailing_legacy, 1)
        self.assertEqual(stats.trailing_activated, 0)
        self.assertEqual(stats.warnings, ())

    def test_invariants_hold_when_trailing_reaches_threshold(self) -> None:
        with connect(self.db_path) as conn:
            self._insert_closed_trade(
                conn,
                slug="btc-updown-5m-trail",
                max_price_seen=0.44,
                trailing_active=True,
                trailing_activation_price=0.43,
                highest_price=0.44,
                exit_reason="TRAILING_STOP",
            )
            conn.commit()
            stats = fetch_trailing_strategy_stats(conn, "NO_C")

        self.assertEqual(stats.trailing_activated, 1)
        self.assertEqual(stats.exited_by_trailing, 1)
        self.assertEqual(stats.reach_at_activation_threshold, 1)
        self.assertEqual(stats.warnings, ())
        self.assertAlmostEqual(stats.avg_peak_before_exit, 0.04)
        self.assertAlmostEqual(stats.avg_peak_after_activation, 0.04)
        self.assertAlmostEqual(stats.avg_profit_at_activation, 0.03)

    def test_max_excursion_distribution_and_thresholds(self) -> None:
        with connect(self.db_path) as conn:
            self._insert_closed_trade(
                conn,
                slug="btc-updown-5m-low",
                max_price_seen=0.405,
                exit_reason="STOP_LOSS",
            )
            self._insert_closed_trade(
                conn,
                slug="btc-updown-5m-mid",
                max_price_seen=0.425,
                exit_reason="STOP_LOSS",
            )
            self._insert_closed_trade(
                conn,
                slug="btc-updown-5m-high",
                max_price_seen=0.45,
                trailing_active=True,
                trailing_activation_price=0.43,
                highest_price=0.45,
                exit_reason="TRAILING_STOP",
            )
            conn.commit()
            stats = fetch_trailing_strategy_stats(conn, "NO_C")

        bucket_map = dict(stats.max_excursion.buckets)
        self.assertEqual(bucket_map["0.00–0.01"], 1)
        self.assertEqual(bucket_map["0.02–0.03"], 1)
        self.assertEqual(bucket_map["0.04+"], 1)

        threshold_map = dict(stats.max_excursion.threshold_rates)
        self.assertAlmostEqual(threshold_map["+0.02"], 2 / 3)
        self.assertAlmostEqual(threshold_map["+0.03"], 1 / 3)
        self.assertAlmostEqual(threshold_map["+0.04"], 1 / 3)

    def test_format_trailing_summary_contains_required_fields(self) -> None:
        with connect(self.db_path) as conn:
            self._insert_closed_trade(conn, slug="btc-updown-5m-format")
            conn.commit()
            summary = format_trailing_summary(conn)

        self.assertIn("Trailing Summary", summary)
        self.assertIn("NO_C", summary)
        self.assertIn("Entries:", summary)
        self.assertIn("Trailing Activated:", summary)
        self.assertIn("Activation Rate:", summary)
        self.assertIn("Average Peak Before Exit:", summary)
        self.assertIn("Average Peak After Activation:", summary)
        self.assertIn("Exited By Trailing:", summary)
        self.assertIn("Exited By Stop Loss:", summary)
        self.assertIn("Exited By Time Stop:", summary)
        self.assertIn("Average Profit At Activation:", summary)
        self.assertIn("Max Excursion", summary)
        self.assertIn("0.00–0.01", summary)
        self.assertIn("Reach +0.02:", summary)
        self.assertIn("Exited By Trailing (legacy):", summary)
        self.assertIn("Sample trades", summary)


if __name__ == "__main__":
    unittest.main()
