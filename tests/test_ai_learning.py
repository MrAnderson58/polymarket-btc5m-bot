"""Tests for Daily Learning pipeline."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.ai_agent.learning import process_all_trades, run_daily_learning
from bot.ai_agent.memory import load_ai_decisions
from bot.database import connect, init_db
from tests.ai_agent_helpers import seed_trades


class LearningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_process_all_trades(self) -> None:
        state_path = Path(self._tmpdir.name) / "ai_agent_state.json"
        with mock.patch("bot.ai_agent.learning._state_path", return_value=state_path):
            with mock.patch("bot.ai_agent.journal.journal_root", return_value=Path(self._tmpdir.name) / "ai_journal"):
                with connect(self.db_path) as conn:
                    seed_trades(conn, 4)
                    conn.commit()
                    summary = process_all_trades(conn)
                    conn.commit()
                    decisions = load_ai_decisions(conn)
        self.assertEqual(summary["trades_processed"], 4)
        self.assertEqual(len(decisions), 4)
        self.assertTrue(state_path.exists())

    def test_run_daily_learning(self) -> None:
        state_path = Path(self._tmpdir.name) / "state.json"
        with mock.patch("bot.ai_agent.learning._state_path", return_value=state_path):
            with mock.patch("bot.ai_agent.journal.journal_root", return_value=Path(self._tmpdir.name) / "j"):
                with connect(self.db_path) as conn:
                    seed_trades(conn, 2)
                    conn.commit()
                    summary = run_daily_learning(conn)
        self.assertIn("counterfactual", summary)
