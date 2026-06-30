"""Tests for time-windowed ER funnel reporting."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db, insert_early_reversion_v2_trade
from bot.er_stats import (
    format_er_funnel_time_report,
    record_entry_evaluation,
)


class ErFunnelTimeStatsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {"ENABLED_STRATEGIES": "NO_C"},
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

    def test_funnel_time_report_format(self) -> None:
        now_ts = int(time.time())
        with connect(self.db_path) as conn:
            record_entry_evaluation(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=12,
                entry_window_sec=30,
                has_trade=False,
                risk=None,
                would_enter=True,
                market_slug="btc-updown-5m-test",
                eval_ts=now_ts - 120,
            )
            record_entry_evaluation(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.42,
                entry_threshold=0.40,
                seconds_open=45,
                entry_window_sec=30,
                has_trade=False,
                risk=None,
                would_enter=False,
                market_slug="btc-updown-5m-test",
                eval_ts=now_ts - 120,
            )
            insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-entry",
                window_start_ts=now_ts - 300,
                end_ts=now_ts,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.39,
                entry_ts=now_ts - 180,
            )
            conn.commit()
            report = format_er_funnel_time_report(conn, strategy_names=("NO_C",))

        self.assertIn("ER FUNNEL BY TIME", report)
        self.assertIn("Last 1 hour", report)
        self.assertIn("Last 6 hours", report)
        self.assertIn("NO_C", report)
        self.assertIn("Checks: 2", report)
        self.assertIn("Window OK: 1", report)
        self.assertIn("Price OK: 1", report)
        self.assertIn("Ready: 1", report)
        self.assertIn("Entries: 1", report)
