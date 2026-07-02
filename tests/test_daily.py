"""Smoke test for daily pipeline (quick mode)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.daily.pipeline import run_daily_pipeline
from tests.ai_agent_helpers import seed_trades


class DailyPipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_daily_pipeline_quick(self) -> None:
        state = Path(self._tmpdir.name) / "ai_state.json"
        journal = Path(self._tmpdir.name) / "ai_journal"
        opt_cache = Path(self._tmpdir.name) / "optimizer_cache"
        review_cache = Path(self._tmpdir.name) / "strategy_review_cache"
        opt_cache.mkdir(parents=True, exist_ok=True)
        review_cache.mkdir(parents=True, exist_ok=True)

        with mock.patch("bot.ai_agent.learning._state_path", return_value=state):
            with mock.patch("bot.ai_agent.journal.journal_root", return_value=journal):
                with mock.patch("bot.optimizer.cache.CACHE_DIR", opt_cache):
                    with mock.patch(
                        "bot.optimizer.cache.RESULTS_PATH",
                        opt_cache / "optimizer_results.json",
                    ):
                        with mock.patch("bot.strategy_review.cache.CACHE_DIR", review_cache):
                            with mock.patch(
                                "bot.strategy_review.cache.RESULTS_PATH",
                                review_cache / "strategy_review_results.json",
                            ):
                                with connect(self.db_path) as conn:
                                    seed_trades(conn, 6)
                                    conn.commit()
                                    summary = run_daily_pipeline(
                                        conn, quick_optimizer=True, save_files=False
                                    )
        self.assertIn("optimizer", summary)
        self.assertIn("brain", summary)
        self.assertIn("scientist", summary)


if __name__ == "__main__":
    unittest.main()
