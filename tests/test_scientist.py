"""Tests for Trading AI Scientist v1."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.scientist.builder import build_scientist_section
from bot.scientist.hypothesis import generate_hypotheses, is_duplicate
from bot.scientist.ranking import rank_hypothesis
from bot.scientist.render import render_scientist_markdown, write_scientist_report
from bot.scientist.research import discover_patterns
from bot.scientist.scheduler import run_scientist_cycle
from bot.scientist.validation import validate_hypothesis
from tests.ai_agent_helpers import seed_trades


class ScientistTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_generate_and_validate_hypothesis(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 12)
            conn.commit()
            patterns = discover_patterns(conn)
            hypotheses = generate_hypotheses(conn)
            conn.commit()
            self.assertIsInstance(patterns, list)
            if hypotheses:
                v = validate_hypothesis(conn, hypotheses[0])
                self.assertIn(v["status"], ("PASSED", "FAILED", "REJECTED"))
                self.assertIn("checks", v)
                ranking = rank_hypothesis(hypotheses[0], v)
                self.assertIn(ranking["priority"], ("HIGH", "MEDIUM", "LOW"))

    def test_duplicate_detector(self) -> None:
        fp = "test_fingerprint_abc"
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO scientist_hypotheses (
                    fingerprint, description, source, sample_n, confidence,
                    hypothesis_type, params_json
                ) VALUES (?, 'd', 't', 10, 50, 'test', '{}')
                """,
                (fp,),
            )
            conn.commit()
            self.assertTrue(is_duplicate(conn, fp))
            self.assertFalse(is_duplicate(conn, "other"))

    def test_run_scientist_cycle(self) -> None:
        out_dir = Path(self._tmpdir.name) / "scientist_reports"
        with connect(self.db_path) as conn:
            seed_trades(conn, 8)
            conn.commit()
            result = run_scientist_cycle(conn)
            conn.commit()

        self.assertIn("summary", result)
        self.assertIn("best_next_step", result)
        self.assertTrue(result["best_next_step"]["blocked"])

        md = render_scientist_markdown(result)
        self.assertIn("NEW HYPOTHESES", md)
        self.assertIn("BEST NEXT STEP", md)

        with mock.patch("bot.scientist.render.BASE_DIR", Path(self._tmpdir.name)):
            md_path, json_path = write_scientist_report(result, out_dir=out_dir)
        self.assertTrue(md_path.exists())
        self.assertTrue(json_path.exists())

    def test_build_scientist_section(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 5)
            conn.commit()
            section = build_scientist_section(conn, run_cycle=True)

        self.assertEqual(section["version"], "1.0")
        self.assertEqual(section["mode"], "observe_only")
        self.assertIn("quality_rules", section)


if __name__ == "__main__":
    unittest.main()
