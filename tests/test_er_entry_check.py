"""Tests for Early Reversion entry diagnostics."""

from __future__ import annotations

import unittest

from bot.early_reversion import SIGNALS
from bot.er_entry_check import evaluate_signal_entry, log_version_entry_check
from bot.risk import RiskCheckResult


class ErEntryCheckTestCase(unittest.TestCase):
    def _signal(self, name: str):
        return next(signal for signal in SIGNALS if signal.strategy_name == name)

    def test_entry_blocked_by_window(self) -> None:
        diagnostic = evaluate_signal_entry(
            self._signal("NO_C"),
            seconds_open=143,
            entry_window_sec=30,
            quotes={"no_ask": 0.39},
            has_trade=False,
        )
        self.assertFalse(diagnostic.would_enter)
        self.assertIn("seconds_from_start", diagnostic.blocked_by or "")

    def test_entry_passes_price_and_window(self) -> None:
        diagnostic = evaluate_signal_entry(
            self._signal("NO_C"),
            seconds_open=12,
            entry_window_sec=30,
            quotes={"no_ask": 0.39},
            has_trade=False,
            risk=RiskCheckResult(allowed=True),
        )
        self.assertTrue(diagnostic.would_enter)
        self.assertIsNone(diagnostic.blocked_by)

    def test_entry_blocked_by_price(self) -> None:
        diagnostic = evaluate_signal_entry(
            self._signal("NO_C"),
            seconds_open=12,
            entry_window_sec=30,
            quotes={"no_ask": 0.41},
            has_trade=False,
        )
        self.assertFalse(diagnostic.would_enter)
        self.assertIn("ask", diagnostic.blocked_by or "")

    def test_log_version_entry_check_returns_signal_name(self) -> None:
        diagnostic = evaluate_signal_entry(
            self._signal("NO_C"),
            seconds_open=12,
            entry_window_sec=30,
            quotes={"no_ask": 0.39},
            has_trade=False,
            risk=RiskCheckResult(allowed=True),
        )
        with self.assertLogs("bot.er_entry_check", level="INFO") as logs:
            active = log_version_entry_check("V2", [diagnostic])
        self.assertEqual(active, "NO_C")
        self.assertIn("V2 CHECK", logs.output[0])
        self.assertIn("RESULT = NO_C", logs.output[0])


if __name__ == "__main__":
    unittest.main()
