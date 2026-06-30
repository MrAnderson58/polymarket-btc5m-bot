"""Tests for early-window pipeline log gating."""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from bot.er_pipeline import (
    log_early_window_once,
    log_pipeline,
    pipeline_logs_enabled,
    set_pipeline_log_context,
)


class ErPipelineLogWindowTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        set_pipeline_log_context(window_start_ts=0, seconds_from_start=999.0)

    def test_pipeline_logs_enabled_only_in_first_30_seconds(self) -> None:
        set_pipeline_log_context(window_start_ts=1_700_000_000, seconds_from_start=12.0)
        self.assertTrue(pipeline_logs_enabled())

        set_pipeline_log_context(window_start_ts=1_700_000_000, seconds_from_start=31.0)
        self.assertFalse(pipeline_logs_enabled())

    def test_log_pipeline_suppressed_after_30_seconds(self) -> None:
        set_pipeline_log_context(window_start_ts=1_700_000_000, seconds_from_start=45.0)
        with mock.patch.object(logging.getLogger("bot.er_pipeline"), "info") as mock_info:
            log_pipeline(7, strategy="NO_C")
        mock_info.assert_not_called()

    def test_early_window_logged_once_per_strategy(self) -> None:
        set_pipeline_log_context(window_start_ts=1_700_000_000, seconds_from_start=10.0)
        with mock.patch.object(logging.getLogger("bot.er_pipeline"), "info") as mock_info:
            log_early_window_once(
                strategy_name="NO_C",
                ask=0.38,
                seconds_from_start=10.0,
            )
            log_early_window_once(
                strategy_name="NO_C",
                ask=0.39,
                seconds_from_start=15.0,
            )
        self.assertEqual(mock_info.call_count, 1)
        self.assertIn("EARLY WINDOW", mock_info.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
