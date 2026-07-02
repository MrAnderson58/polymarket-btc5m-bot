"""Tests for Trading AI Strategy Review v1."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.report.memory import set_reports_root
from bot.strategy_review.builder import build_strategy_review
from bot.strategy_review.entry import analyze_entry
from bot.strategy_review.render import render_strategy_review_section
from bot.strategy_review.safety import check_safety_gate
from bot.strategy_review.verdict import build_final_verdict
from tests.ai_agent_helpers import seed_trades


class StrategyReviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.reports_dir = Path(self._tmpdir.name) / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        set_reports_root(self.reports_dir)
        init_db(self.db_path)

    def tearDown(self) -> None:
        set_reports_root(None)
        self._tmpdir.cleanup()

    def _minimal_report(self, trades: int = 50) -> dict:
        return {
            "live_sample": {
                "current_since_change": trades,
                "total_trades": trades,
                "sufficient": trades >= 300,
            },
            "configuration": {
                "stop_loss_pct": -10.0,
                "trailing_activation": 0.03,
                "trailing_distance": 0.01,
            },
            "scientist": {"best_next_step": {"blocked": True}},
            "trading_brain": {},
        }

    def test_entry_analysis_rows(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 8)
            conn.commit()
            entry = analyze_entry(conn)
        self.assertEqual(len(entry["rows"]), 7)
        self.assertIn("recommendation", entry)

    def test_safety_gate_blocks_low_sample(self) -> None:
        report = self._minimal_report(trades=50)
        gate = check_safety_gate(
            report,
            {
                "confidence_pct": 90,
                "p_value": 0.01,
                "walk_forward": {"passed": True},
                "overfit_risk": "LOW",
                "sample_size": 400,
            },
        )
        self.assertFalse(gate["passed"])
        self.assertTrue(any("300" in f for f in gate["failures"]))

    def test_final_verdict_keep_on_insufficient_live_sample(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 6)
            conn.commit()
            entry = analyze_entry(conn)
            from bot.strategy_review.stop_loss import analyze_stop
            from bot.strategy_review.trailing import analyze_trailing

            stop = analyze_stop(conn)
            trailing = analyze_trailing(conn)
            verdict = build_final_verdict(
                self._minimal_report(trades=20),
                entry=entry,
                stop=stop,
                trailing=trailing,
            )
        self.assertEqual(verdict["decision"], "KEEP CURRENT SETTINGS")
        self.assertTrue(verdict["safety_blocked"])

    def test_build_strategy_review(self) -> None:
        state = Path(self._tmpdir.name) / "ai_state.json"
        journal = Path(self._tmpdir.name) / "ai_journal"
        with mock.patch("bot.ai_agent.learning._state_path", return_value=state):
            with mock.patch("bot.ai_agent.journal.journal_root", return_value=journal):
                with connect(self.db_path) as conn:
                    seed_trades(conn, 4)
                    conn.commit()
                    report = self._minimal_report(trades=20)
                    review = build_strategy_review(conn, report)

        self.assertEqual(review["version"], "1.0")
        self.assertIn("final_verdict", review)
        self.assertIn("combinations", review)
        md_lines = render_strategy_review_section(review)
        self.assertTrue(any("FINAL STRATEGY REVIEW" in line for line in md_lines))


if __name__ == "__main__":
    unittest.main()
