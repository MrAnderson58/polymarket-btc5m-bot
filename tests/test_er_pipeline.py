"""Tests for ENTRY PIPELINE diagnostics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.early_reversion_v2 import _try_open_signals
from bot.er_entry_check import evaluate_signal_entry
from bot.early_reversion import EarlyReversionSignal
from bot.market_scanner import Btc5mMarket, TokenQuotes


class ErPipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self.signal = EarlyReversionSignal("NO_C", "NO", 0.40, 0.45)
        self.market = Btc5mMarket(
            slug="btc-updown-5m-test",
            title="test",
            condition_id="cond",
            window_start_ts=1,
            end_ts=301,
            yes_token_id="yes",
            no_token_id="no",
            yes_outcome="Yes",
            no_outcome="No",
            yes_quotes=TokenQuotes(token_id="yes", bid=0.61, ask=0.63),
            no_quotes=TokenQuotes(token_id="no", bid=0.37, ask=0.42),
        )
        self.quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.37,
            "no_ask": 0.42,
        }

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_try_open_ask_skip_records_other(self) -> None:
        eval_quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.37,
            "no_ask": 0.38,
        }
        try_quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.37,
            "no_ask": 0.42,
        }
        diagnostics = [
            evaluate_signal_entry(
                self.signal,
                seconds_open=12,
                entry_window_sec=30,
                quotes=eval_quotes,
                has_trade=False,
            )
        ]
        with connect(self.db_path) as conn:
            _try_open_signals(
                conn,
                self.market,
                try_quotes,
                now_ts=100,
                diagnostics=diagnostics,
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT COALESCE(blocked_other, 0) AS blocked_other
                FROM er_strategy_counters
                WHERE strategy_version = 'v2' AND strategy_name = 'NO_C'
                """
            ).fetchone()

        self.assertEqual(int(row["blocked_other"]), 1)

    def test_try_open_has_trade_skip_records_existing_position(self) -> None:
        diagnostics = [
            evaluate_signal_entry(
                self.signal,
                seconds_open=12,
                entry_window_sec=30,
                quotes={
                    "yes_bid": 0.61,
                    "yes_ask": 0.63,
                    "no_bid": 0.37,
                    "no_ask": 0.38,
                },
                has_trade=False,
            )
        ]
        with connect(self.db_path) as conn:
            with mock.patch(
                "bot.early_reversion_v2.has_early_reversion_v2_trade",
                return_value=True,
            ):
                _try_open_signals(
                    conn,
                    self.market,
                    self.quotes,
                    now_ts=100,
                    diagnostics=diagnostics,
                )
            conn.commit()
            row = conn.execute(
                """
                SELECT COALESCE(blocked_existing_position, 0) AS blocked_existing_position
                FROM er_strategy_counters
                WHERE strategy_version = 'v2' AND strategy_name = 'NO_C'
                """
            ).fetchone()

        self.assertEqual(int(row["blocked_existing_position"]), 1)

    def test_risk_failed_at_eval_does_not_double_count_on_ask_skip(self) -> None:
        from bot.risk import RiskCheckResult

        diagnostics = [
            evaluate_signal_entry(
                self.signal,
                seconds_open=12,
                entry_window_sec=30,
                quotes={
                    "yes_bid": 0.61,
                    "yes_ask": 0.63,
                    "no_bid": 0.37,
                    "no_ask": 0.38,
                },
                has_trade=False,
                risk=RiskCheckResult(allowed=False, reason="MAX_OPEN_POSITIONS reached"),
            )
        ]
        with connect(self.db_path) as conn:
            _try_open_signals(
                conn,
                self.market,
                self.quotes,
                now_ts=100,
                diagnostics=diagnostics,
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT COALESCE(blocked_max_open_positions, 0) AS blocked_max_open_positions,
                       COALESCE(blocked_other, 0) AS blocked_other,
                       COALESCE(blocked_unknown, 0) AS blocked_unknown
                FROM er_strategy_counters
                WHERE strategy_version = 'v2' AND strategy_name = 'NO_C'
                """
            ).fetchone()

        if row is None:
            blocked_max_open = blocked_other = blocked_unknown = 0
        else:
            blocked_max_open = int(row["blocked_max_open_positions"])
            blocked_other = int(row["blocked_other"])
            blocked_unknown = int(row["blocked_unknown"])

        self.assertEqual(blocked_max_open, 0)
        self.assertEqual(blocked_other, 0)
        self.assertEqual(blocked_unknown, 0)


if __name__ == "__main__":
    unittest.main()
