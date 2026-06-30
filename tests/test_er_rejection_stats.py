"""Tests for Early Reversion rejection reason counters."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.er_entry_check import evaluate_version_entries
from bot.er_stats import (
    fetch_strategy_counters,
    format_er_summary,
    record_entry_attempt,
    record_entry_evaluation,
    record_entry_success,
)
from bot.early_reversion import EarlyReversionSignal
from bot.risk import RiskCheckResult


class ErRejectionStatsTestCase(unittest.TestCase):
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

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_sequential_funnel_counters(self) -> None:
        with connect(self.db_path) as conn:
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
            )
            record_entry_evaluation(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=12,
                entry_window_sec=30,
                has_trade=False,
                risk=RiskCheckResult(allowed=False, reason="MAX_OPEN_POSITIONS reached"),
                would_enter=False,
            )
            record_entry_evaluation(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=12,
                entry_window_sec=30,
                has_trade=False,
                risk=RiskCheckResult(allowed=True),
                would_enter=True,
            )
            record_entry_attempt(conn, "v2", "NO_C")
            record_entry_success(conn, "v2", "NO_C")
            conn.commit()

            summary = format_er_summary(conn)
            counters = fetch_strategy_counters(conn, "v2")[0]

        self.assertIn("checks: 3", summary)
        self.assertEqual(counters.entry_success, 1)
        self.assertEqual(counters.window_ok, 2)
        self.assertEqual(counters.price_ok, 2)
        self.assertEqual(counters.window_and_price_ok, 2)
        self.assertEqual(counters.already_open_ok, 2)
        self.assertEqual(counters.risk_ok, 1)
        self.assertEqual(counters.entry_attempt, 1)
        self.assertEqual(counters.entry_success, 1)
        self.assertEqual(counters.blocked_by_window, 1)
        self.assertEqual(counters.blocked_by_price, 1)
        self.assertEqual(counters.blocked_max_open_positions, 1)
        self.assertEqual(counters.blocked_existing_position, 0)
        self.assertEqual(counters.blocked_unknown, 0)

    def test_unknown_block_when_risk_ok_zero_without_reason(self) -> None:
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
                would_enter=False,
            )
            conn.commit()
            counters = fetch_strategy_counters(conn, "v2")[0]

        self.assertEqual(counters.already_open_ok, 1)
        self.assertEqual(counters.risk_ok, 0)
        self.assertEqual(counters.blocked_unknown, 1)

    def test_existing_position_block_reason(self) -> None:
        with connect(self.db_path) as conn:
            record_entry_evaluation(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=12,
                entry_window_sec=30,
                has_trade=True,
                risk=None,
                would_enter=False,
            )
            conn.commit()
            counters = fetch_strategy_counters(conn, "v2")[0]

        self.assertEqual(counters.blocked_by_already_open, 1)
        self.assertEqual(counters.blocked_existing_position, 1)

    def test_entry_success_never_exceeds_risk_ok(self) -> None:
        with connect(self.db_path) as conn:
            record_entry_evaluation(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=10,
                entry_window_sec=30,
                has_trade=False,
                risk=RiskCheckResult(allowed=True),
                would_enter=True,
            )
            record_entry_attempt(conn, "v2", "NO_C")
            record_entry_success(conn, "v2", "NO_C")
            conn.commit()
            summary = format_er_summary(conn)
            counters = fetch_strategy_counters(conn, "v2")[0]

        self.assertIn("entries: 0", summary)
        self.assertEqual(counters.entry_success, 1)
        self.assertEqual(counters.risk_ok, 1)

    def test_evaluate_version_entries_records_funnel(self) -> None:
        signal = EarlyReversionSignal("NO_C", "NO", 0.40, 0.45)
        quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.37,
            "no_ask": 0.42,
        }

        with connect(self.db_path) as conn:
            with mock.patch(
                "bot.er_entry_check.check_can_open_position",
                return_value=RiskCheckResult(allowed=True),
            ):
                evaluate_version_entries(
                    conn,
                    strategy_version="v2",
                    active_signals=(signal,),
                    market_slug="btc-updown-5m-test",
                    seconds_open=20,
                    entry_window_sec=30,
                    quotes=quotes,
                    has_trade_fn=lambda *_args, **_kwargs: False,
                )
            conn.commit()
            summary = format_er_summary(conn)
            counters = fetch_strategy_counters(conn, "v2")[0]

        self.assertIn("checks: 1", summary)
        self.assertEqual(counters.blocked_by_price, 1)
        self.assertEqual(counters.blocked_by_window, 0)
        self.assertEqual(counters.risk_ok, 0)


if __name__ == "__main__":
    unittest.main()
