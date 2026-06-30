"""Tests for execution audit logging helpers."""

from __future__ import annotations

import unittest

from bot.execution_audit import (
    _validate_entry_price,
    _validate_exit_price,
    _validate_side_token,
    compute_trade_pnl,
    entry_trace_stop_reason,
    log_attempt_entry_result,
    log_entry_trace_start,
    log_entry_trace_stop,
)


class ExecutionAuditTestCase(unittest.TestCase):
    def test_buy_uses_same_side_ask(self) -> None:
        quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.37,
            "no_ask": 0.40,
        }
        warnings = _validate_entry_price(
            side="NO",
            quotes=quotes,
            selected_price=0.40,
            submitted_price=0.40,
        )
        self.assertEqual(warnings, [])

    def test_buy_warns_when_using_opposite_side_price(self) -> None:
        quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.37,
            "no_ask": 0.40,
        }
        warnings = _validate_entry_price(
            side="NO",
            quotes=quotes,
            selected_price=0.63,
            submitted_price=0.63,
        )
        self.assertTrue(any("opposite token YES ask" in item for item in warnings))
        self.assertTrue(any("!= NO ask" in item for item in warnings))

    def test_sell_uses_same_side_bid(self) -> None:
        quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.35,
            "no_ask": 0.40,
        }
        warnings = _validate_exit_price(
            side="NO",
            quotes=quotes,
            selected_price=0.35,
            submitted_price=0.35,
        )
        self.assertEqual(warnings, [])

    def test_token_side_validation(self) -> None:
        warnings = _validate_side_token(
            side="NO",
            token_id="no-token",
            yes_token_id="yes-token",
            no_token_id="no-token",
        )
        self.assertEqual(warnings, [])

        mismatch = _validate_side_token(
            side="NO",
            token_id="yes-token",
            yes_token_id="yes-token",
            no_token_id="no-token",
        )
        self.assertEqual(len(mismatch), 1)

    def test_pnl_formula(self) -> None:
        entry_cost, exit_value, profit_usdc, profit_pct = compute_trade_pnl(
            entry_price=0.40,
            exit_price=0.35,
            shares=5.0,
        )
        self.assertAlmostEqual(entry_cost, 2.0)
        self.assertAlmostEqual(exit_value, 1.75)
        self.assertAlmostEqual(profit_usdc, -0.25)
        self.assertAlmostEqual(profit_pct, -12.5)

    def test_attempt_entry_result_log_format(self) -> None:
        with self.assertLogs("bot.execution_audit", level="INFO") as logs:
            log_attempt_entry_result(
                strategy_name="NO_C",
                market_slug="btc-updown-5m-test",
                success=False,
                reason="duplicate",
                detail="v2:NO_C:btc-updown-5m-test:NO:entry",
            )
        output = "\n".join(logs.output)
        self.assertIn("ATTEMPT ENTRY RESULT", output)
        self.assertIn("strategy=NO_C", output)
        self.assertIn("market=btc-updown-5m-test", output)
        self.assertIn("result=FAILED", output)
        self.assertIn("reason=duplicate", output)

    def test_entry_trace_start_format(self) -> None:
        with self.assertLogs("bot.execution_audit", level="INFO") as logs:
            log_entry_trace_start(
                strategy_name="NO_C",
                market_slug="btc-updown-5m-test",
                price=0.40,
                shares=2.5,
            )
        output = "\n".join(logs.output)
        self.assertIn("ENTRY TRACE START", output)
        self.assertIn("strategy=NO_C", output)
        self.assertIn("market=btc-updown-5m-test", output)
        self.assertIn("price=0.4", output)
        self.assertIn("shares=2.5", output)

    def test_entry_trace_stop_reason_mapping(self) -> None:
        self.assertEqual(
            entry_trace_stop_reason("duplicate", context="idempotency"),
            "idempotency",
        )
        self.assertEqual(
            entry_trace_stop_reason("duplicate", context="existing_position"),
            "existing_position",
        )
        self.assertEqual(entry_trace_stop_reason("other"), "live_mode")

    def test_entry_trace_stop_format(self) -> None:
        with self.assertLogs("bot.execution_audit", level="INFO") as logs:
            log_entry_trace_stop("max_open_positions")
        output = "\n".join(logs.output)
        self.assertIn("ENTRY TRACE STOP", output)
        self.assertIn("reason=max_open_positions", output)


if __name__ == "__main__":
    unittest.main()
