"""Tests for ER SUMMARY reporting format."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import (
    close_early_reversion_v2_trade,
    close_early_reversion_v25_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
    insert_early_reversion_v25_trade,
)
from bot.er_stats import (
    _fetch_strategy_trade_stats,
    format_er_summary,
    record_entry_success,
    record_strategy_check,
)


class ErSummaryFormatTestCase(unittest.TestCase):
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
        import bot.er_stats as er_stats

        importlib.reload(config)
        importlib.reload(er_stats)
        self.er_stats = er_stats

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_summary_lists_enabled_strategies_separately(self) -> None:
        now = int(time.time())
        with connect(self.db_path) as conn:
            record_strategy_check(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=12,
            )
            insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-open",
                window_start_ts=now,
                end_ts=now + 300,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=now,
            )
            record_entry_success(conn, "v2", "NO_C")
            conn.commit()
            summary = self.er_stats.format_er_summary(conn)

        self.assertIn("YES_C", summary)
        self.assertIn("NO_C", summary)
        self.assertIn("checks: 1", summary)
        self.assertIn("entries: 1", summary)
        self.assertIn("closed_trades: 0", summary)
        self.assertNotIn("window_ok:", summary)

    def test_summary_includes_wins_and_avg_pnl(self) -> None:
        now = int(time.time())
        with connect(self.db_path) as conn:
            trade_id = insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-test",
                window_start_ts=now,
                end_ts=now + 300,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=now,
            )
            close_early_reversion_v2_trade(
                conn,
                trade_id,
                exit_price=0.44,
                exit_reason="TRAILING_STOP",
                pnl_percent=10.0,
                pnl_usdc=0.20,
                holding_time_seconds=30.0,
            )
            record_entry_success(conn, "v2", "NO_C")
            conn.commit()
            summary = self.er_stats.format_er_summary(conn)
            stats = _fetch_strategy_trade_stats(conn, "NO_C")

        self.assertEqual(stats.entries, 1)
        self.assertEqual(stats.closed_trades, 1)
        self.assertEqual(stats.wins, 1)
        self.assertEqual(stats.losses, 0)
        self.assertEqual(stats.closed_trades, stats.wins + stats.losses)
        self.assertIn("wins: 1", summary)
        self.assertIn("losses: 0", summary)
        self.assertIn("win_rate: 100.0%", summary)
        self.assertIn("avg pnl: +10.00%", summary)

    def test_dedupes_same_market_across_versions(self) -> None:
        now = int(time.time())
        slug = "btc-updown-5m-dedupe"
        with connect(self.db_path) as conn:
            v2_id = insert_early_reversion_v2_trade(
                conn,
                market_slug=slug,
                window_start_ts=now,
                end_ts=now + 300,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=now,
            )
            close_early_reversion_v2_trade(
                conn,
                v2_id,
                exit_price=0.44,
                exit_reason="TRAILING_STOP",
                pnl_percent=10.0,
                pnl_usdc=0.20,
                holding_time_seconds=30.0,
            )
            v25_id = insert_early_reversion_v25_trade(
                conn,
                market_slug=slug,
                window_start_ts=now,
                end_ts=now + 300,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=now + 1,
            )
            close_early_reversion_v25_trade(
                conn,
                v25_id,
                exit_price=0.36,
                exit_reason="STOP_LOSS",
                pnl_percent=-10.0,
                pnl_usdc=-0.20,
                holding_time_seconds=40.0,
            )
            conn.commit()
            stats = _fetch_strategy_trade_stats(conn, "NO_C")

        self.assertEqual(stats.entries, 1)
        self.assertEqual(stats.closed_trades, 1)
        self.assertEqual(stats.losses, 1)
        self.assertEqual(stats.wins, 0)
        self.assertEqual(stats.wins + stats.losses, stats.closed_trades)

    def test_open_and_closed_trades_sum_to_entries(self) -> None:
        now = int(time.time())
        with connect(self.db_path) as conn:
            open_id = insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-open",
                window_start_ts=now,
                end_ts=now + 300,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=now,
            )
            closed_id = insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-closed",
                window_start_ts=now + 300,
                end_ts=now + 600,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=now + 300,
            )
            close_early_reversion_v2_trade(
                conn,
                closed_id,
                exit_price=0.36,
                exit_reason="STOP_LOSS",
                pnl_percent=-10.0,
                pnl_usdc=-0.20,
                holding_time_seconds=30.0,
            )
            conn.commit()
            stats = _fetch_strategy_trade_stats(conn, "NO_C")

        self.assertEqual(open_id, 1)
        self.assertEqual(stats.entries, 2)
        self.assertEqual(stats.open_trades, 1)
        self.assertEqual(stats.closed_trades, 1)
        self.assertEqual(stats.entries, stats.open_trades + stats.closed_trades)
        self.assertLessEqual(stats.closed_trades, stats.entries)

    def test_inactive_strategy_warning(self) -> None:
        now = int(time.time())
        with connect(self.db_path) as conn:
            trade_id = insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-active",
                window_start_ts=now,
                end_ts=now + 300,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=now,
            )
            close_early_reversion_v2_trade(
                conn,
                trade_id,
                exit_price=0.44,
                exit_reason="TRAILING_STOP",
                pnl_percent=10.0,
                pnl_usdc=0.20,
                holding_time_seconds=30.0,
            )
            conn.commit()
            summary = self.er_stats.format_er_summary(conn)

        self.assertIn("Strategy inactive: YES_C", summary)
        self.assertNotIn("Strategy inactive: NO_C", summary)


if __name__ == "__main__":
    unittest.main()
