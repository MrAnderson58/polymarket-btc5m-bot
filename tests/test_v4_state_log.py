"""Tests for V4 state-machine diagnostics."""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from bot.v4.state_log import blocked_signature, log_blocked, log_state_transition


class V4StateLogTestCase(unittest.TestCase):
    def test_log_state_transition(self) -> None:
        with self.assertLogs("bot.v4.state_log", level="INFO") as captured:
            log_state_transition(
                "OBSERVE",
                elapsed=37,
                score=6.0,
                probability=0.58,
            )
        output = "\n".join(captured.output)
        self.assertIn("V4 STATE | OBSERVE", output)
        self.assertIn("elapsed=37", output)
        self.assertIn("score=6.0", output)
        self.assertIn("probability=0.58", output)

    def test_log_state_transition_with_arrow(self) -> None:
        with self.assertLogs("bot.v4.state_log", level="INFO") as captured:
            log_state_transition("TREND_FOUND", transition=True, side="NO", score=9.0, probability=0.74)
        output = "\n".join(captured.output)
        self.assertIn("↓", output)
        self.assertIn("V4 STATE | TREND_FOUND", output)

    def test_log_blocked_score(self) -> None:
        with self.assertLogs("bot.v4.state_log", level="INFO") as captured:
            log_blocked("score too low", score=6.0, need=8)
        output = "\n".join(captured.output)
        self.assertIn("V4 BLOCKED", output)
        self.assertIn("score too low", output)
        self.assertIn("score=6.0", output)
        self.assertIn("need=8", output)

    def test_blocked_signature_stable(self) -> None:
        sig_a = blocked_signature("score too low", score=6.0, need=8)
        sig_b = blocked_signature("score too low", score=6.0, need=8)
        self.assertEqual(sig_a, sig_b)


if __name__ == "__main__":
    unittest.main()
