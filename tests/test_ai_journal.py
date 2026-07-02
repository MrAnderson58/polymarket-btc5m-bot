"""Tests for AI Trade Journal."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.ai_agent.journal import journal_root, write_trade_journal


class JournalTestCase(unittest.TestCase):
    def test_write_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("bot.ai_agent.journal.journal_root", return_value=Path(tmp) / "ai_journal"):
                path = write_trade_journal(
                    trade_id=563,
                    payload={
                        "strategy_name": "NO_C",
                        "decision": "ALLOW",
                        "ai_score": 84,
                        "confidence": 92,
                        "similar_count": 311,
                        "historical_pf": 2.83,
                        "historical_wr": 0.59,
                        "market_regime": "Range",
                        "btc_move_30s": 4.2,
                        "spread": 0.01,
                        "outcome": "STOP_LOSS",
                        "pnl": -19.0,
                        "counterfactual_result": "false_allow",
                        "lesson": "Potential false allow.",
                        "explanation": {
                            "positive": ["+ Range regime"],
                            "negative": ["− Spread slightly elevated"],
                        },
                    },
                    closed_at="2026-07-01T12:00:00",
                )
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("Trade #563", text)
            self.assertIn("ALLOW", text)
            self.assertIn("false allow", text.lower())
