"""Tests for Trading Evolution v1 — decision engine (read-only)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.evolution.builder import build_evolution
from bot.evolution.decision import decide_evolution
from bot.evolution.render import render_evolution_block, render_evolution_section
from bot.evolution.state import EvolutionStatus
from bot.optimizer.cache import save_optimizer_cache
from bot.strategy_review.cache import save_strategy_review_cache
from tests.ai_agent_helpers import seed_trades


def _minimal_optimizer_cache() -> dict:
    return {
        "meta": {"cached_at": "2026-01-01T00:00:00Z"},
        "parameter_optimizer": {
            "current": {"entry": 0.37, "stop_pct": -10.0},
            "optimal": {"entry": 0.39, "stop_pct": -10.0},
            "expected_improvement_pct": 12.0,
        },
        "walk_forward": {"trend": "stable", "rows": [{"generalizes": True}]},
        "overfit_detector": {"level": "LOW", "overfit": False},
    }


class EvolutionDecisionTestCase(unittest.TestCase):
    def test_keep_on_insufficient_live_sample(self) -> None:
        sources = {
            "optimizer": _minimal_optimizer_cache(),
            "strategy_review": {
                "final_verdict": {
                    "decision": "KEEP CURRENT SETTINGS",
                    "safety_blocked": True,
                    "confidence_pct": 90,
                }
            },
            "scientist": {"best_next_step": {"blocked": True}},
            "trading_brain": {"top_knowledge": [{"feature": "entry"}]},
            "ai_agent": {"decisions": {"ALLOW": 10, "SKIP": 2}},
            "live_sample": {"current_since_change": 50, "total_trades": 742},
        }
        result = decide_evolution(sources)
        self.assertEqual(result["status"], EvolutionStatus.KEEP.value)
        self.assertIsNotNone(result["next_review_trades"])
        self.assertGreater(result["next_review_trades"], 0)

    def test_ready_for_shadow_when_gates_pass(self) -> None:
        sources = {
            "optimizer": _minimal_optimizer_cache(),
            "strategy_review": {
                "final_verdict": {
                    "decision": "CHANGE ENTRY",
                    "change_parameter": "entry",
                    "change_text": "0.37 → 0.39",
                    "safety_blocked": False,
                    "confidence_pct": 94,
                }
            },
            "scientist": {"best_next_step": {"blocked": False}},
            "trading_brain": {"top_knowledge": [{"feature": "entry"}]},
            "ai_agent": {"decisions": {"ALLOW": 10, "SKIP": 2}},
            "live_sample": {"current_since_change": 400, "total_trades": 742},
        }
        result = decide_evolution(sources)
        self.assertEqual(result["status"], EvolutionStatus.READY_FOR_SHADOW.value)
        self.assertIsNotNone(result["candidate"])
        block = render_evolution_block(result)
        self.assertIn("READY FOR SHADOW", block)
        self.assertIn("742 trades", block)
        self.assertIn("94%", block)

    def test_watch_when_safety_blocked(self) -> None:
        sources = {
            "optimizer": _minimal_optimizer_cache(),
            "strategy_review": {
                "final_verdict": {
                    "decision": "CHANGE ENTRY",
                    "change_parameter": "entry",
                    "change_text": "0.37 → 0.39",
                    "safety_blocked": True,
                    "confidence_pct": 94,
                }
            },
            "scientist": {"best_next_step": {"blocked": False}},
            "trading_brain": {"top_knowledge": [{"feature": "entry"}]},
            "ai_agent": {"decisions": {"ALLOW": 10, "SKIP": 2}},
            "live_sample": {"current_since_change": 400, "total_trades": 742},
        }
        result = decide_evolution(sources)
        self.assertEqual(result["status"], EvolutionStatus.WATCH.value)
        self.assertTrue(result["watch_reasons"])

    def test_build_evolution_integration(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "test.db"
        opt_cache = Path(tmpdir.name) / "optimizer_cache"
        review_cache = Path(tmpdir.name) / "strategy_review_cache"
        opt_cache.mkdir()
        review_cache.mkdir()

        save_optimizer_cache(_minimal_optimizer_cache())
        save_strategy_review_cache(
            {
                "cached_at": "2026-01-01T00:00:00Z",
                "final_verdict": {
                    "decision": "KEEP CURRENT SETTINGS",
                    "safety_blocked": True,
                    "confidence_pct": 50,
                },
            }
        )

        with mock.patch("bot.optimizer.cache.CACHE_DIR", opt_cache):
            with mock.patch("bot.optimizer.cache.RESULTS_PATH", opt_cache / "optimizer_results.json"):
                with mock.patch("bot.strategy_review.cache.CACHE_DIR", review_cache):
                    with mock.patch(
                        "bot.strategy_review.cache.RESULTS_PATH",
                        review_cache / "strategy_review_results.json",
                    ):
                        init_db(db_path)
                        with connect(db_path) as conn:
                            seed_trades(conn, 6)
                            conn.commit()
                            evolution = build_evolution(conn)

        self.assertIn("status", evolution)
        section = render_evolution_section(evolution)
        self.assertTrue(any("Phase 1" in line for line in section))


if __name__ == "__main__":
    unittest.main()
