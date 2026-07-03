"""Tests for Trading Evolution v1/v2 — decision engine and shadow layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.evolution.constants import SHADOW_PF_IMPROVEMENT_MIN
from bot.evolution.decision import decide_evolution
from bot.evolution.render import render_evolution_block
from bot.evolution.shadow import (
    _compute_lane_metrics,
    _verdict_from_metrics,
    evaluate_shadow_decision,
    shadow_would_enter,
    sync_running_shadow_evaluations,
)
from bot.evolution.shadow_db import create_shadow_experiment, get_running_shadow
from bot.evolution.state import EvolutionStatus


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
        block = render_evolution_block(result)
        self.assertIn("READY FOR SHADOW", block)
        self.assertIn("Expected PF", block)

    def test_shadow_running_overrides_ready(self) -> None:
        sources = {
            "optimizer": _minimal_optimizer_cache(),
            "strategy_review": {"final_verdict": {}},
            "scientist": {"best_next_step": {}},
            "trading_brain": {},
            "ai_agent": {},
            "live_sample": {},
            "shadow_experiment": {
                "status": "RUNNING",
                "reason": "in progress",
                "candidate": {
                    "parameter": "entry_threshold",
                    "from_value": 0.40,
                    "to_value": 0.39,
                },
                "sample_size": 37,
                "target_sample_size": 200,
                "next_review_trades": 163,
            },
        }
        result = decide_evolution(sources)
        self.assertEqual(result["status"], EvolutionStatus.SHADOW_RUNNING.value)
        block = render_evolution_block(result)
        self.assertIn("37 / 200", block)


class EvolutionShadowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_shadow_would_enter_respects_threshold(self) -> None:
        self.assertTrue(shadow_would_enter(0.38, 0.40))
        decision, _, _ = evaluate_shadow_decision(0.41, shadow_value=0.39)
        self.assertEqual(decision, "WOULD_SKIP")

    def test_verdict_promote_when_pf_and_dd_better(self) -> None:
        live = {"pf": 1.0, "wr": 50.0, "dd": 10.0}
        shadow = {"pf": 1.2, "wr": 55.0, "dd": 8.0}
        self.assertEqual(_verdict_from_metrics(live, shadow), "PROMOTE")
        shadow["pf"] = 1.05
        self.assertEqual(_verdict_from_metrics(live, shadow), "REJECT")

    def test_pf_improvement_requires_ten_percent(self) -> None:
        live = _compute_lane_metrics([5.0, -2.0, 3.0])
        shadow = _compute_lane_metrics([6.0, 0.0, 3.0])
        min_pf = live["pf"] * (1.0 + SHADOW_PF_IMPROVEMENT_MIN)
        self.assertGreaterEqual(shadow["pf"], min_pf)

    def test_create_and_evaluate_shadow_experiment(self) -> None:
        with connect(self.db_path) as conn:
            exp = create_shadow_experiment(
                conn,
                parameter="entry_threshold",
                current_value=0.40,
                shadow_value=0.39,
            )
            conn.execute(
                """
                INSERT INTO early_reversion_v2_trades (
                    market_slug, window_start_ts, end_ts, side, strategy_name,
                    status, entry_price, entry_ts, exit_price, exit_reason,
                    pnl_percent, pnl_usdc, holding_time_seconds, created_at, closed_at
                ) VALUES (
                    'btc-test', 1000, 1300, 'YES', 'NO_C', 'closed', 0.38, 1100,
                    0.42, 'TRAILING_STOP', 10.5, 0.2, 120, datetime('now'), datetime('now')
                )
                """
            )
            conn.commit()
            n = sync_running_shadow_evaluations(conn)
            conn.commit()
            running = get_running_shadow(conn)
        self.assertEqual(n, 1)
        self.assertIsNotNone(running)
        self.assertEqual(int(running["sample_size"]), 1)


class AutoReviewTestCase(unittest.TestCase):
    """Tests for Trading Strategy Auto Review — single best parameter."""

    def test_finds_entry_candidate_with_consensus(self) -> None:
        from bot.evolution.auto_review import find_best_candidate

        sources = {
            "optimizer": {
                "parameter_optimizer": {
                    "current": {"entry": 0.40, "stop_pct": -10.0, "trailing_activation": 0.03, "trailing_distance": 0.01},
                    "optimal": {"entry": 0.39, "stop_pct": -10.0, "trailing_activation": 0.03, "trailing_distance": 0.01},
                    "expected_improvement_pct": 14.0,
                },
                "walk_forward": {"trend": "stable", "rows": [{"generalizes": True}]},
                "overfit_detector": {"level": "LOW"},
            },
            "strategy_review": {
                "final_verdict": {
                    "change_parameter": "entry",
                    "confidence_pct": 92,
                }
            },
            "scientist": {
                "best_next_step": {
                    "blocked": False,
                    "recommendation": "Lower entry threshold to 0.39",
                    "expected_pf_pct": 14.0,
                    "confidence_pct": 88,
                }
            },
            "trading_brain": {"top_knowledge": [{"feature": "entry_threshold", "direction": "negative"}]},
            "ai_agent": {"decisions": {"ALLOW": 15, "SKIP": 5}},
            "live_sample": {"total_trades": 742},
        }
        cand = find_best_candidate(sources)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["parameter"], "entry")
        self.assertEqual(cand["to_value"], 0.39)

    def test_returns_none_when_ai_agent_says_skip(self) -> None:
        from bot.evolution.auto_review import find_best_candidate

        sources = {
            "optimizer": {
                "parameter_optimizer": {
                    "current": {"entry": 0.40},
                    "optimal": {"entry": 0.39},
                    "expected_improvement_pct": 14.0,
                },
                "walk_forward": {"trend": "stable"},
                "overfit_detector": {"level": "LOW"},
            },
            "strategy_review": {"final_verdict": {}},
            "scientist": {"best_next_step": {"blocked": True}},
            "trading_brain": {},
            "ai_agent": {"decisions": {"ALLOW": 2, "SKIP": 10}},
            "live_sample": {"total_trades": 742},
        }
        cand = find_best_candidate(sources)
        self.assertIsNone(cand)

    def test_picks_single_best_parameter(self) -> None:
        from bot.evolution.auto_review import find_best_candidate

        sources = {
            "optimizer": {
                "parameter_optimizer": {
                    "current": {"entry": 0.40, "stop_pct": -10.0, "trailing_activation": 0.03},
                    "optimal": {"entry": 0.39, "stop_pct": -15.0, "trailing_activation": 0.04},
                    "expected_improvement_pct": 8.0,
                },
                "walk_forward": {"trend": "stable"},
                "overfit_detector": {"level": "LOW"},
            },
            "strategy_review": {"final_verdict": {"change_parameter": "stop_loss", "confidence_pct": 85}},
            "scientist": {
                "best_next_step": {
                    "blocked": False,
                    "recommendation": "Widen stop loss to -15%",
                    "expected_pf_pct": 10.0,
                    "confidence_pct": 85,
                }
            },
            "trading_brain": {"top_knowledge": [{"feature": "stop_loss", "direction": "positive"}]},
            "ai_agent": {"decisions": {"ALLOW": 10, "SKIP": 5}},
            "live_sample": {"total_trades": 500},
        }
        cand = find_best_candidate(sources)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["parameter"], "stop_loss")
        self.assertEqual(cand["to_value"], -15.0)

    def test_never_two_shadows_at_once(self) -> None:
        with connect(self.db_path) as conn:
            from bot.evolution.shadow import maybe_create_shadow_experiment

            exp1 = maybe_create_shadow_experiment(
                conn,
                candidate={"parameter": "entry", "from_value": 0.40, "to_value": 0.39},
                ready_for_shadow=True,
            )
            exp2 = maybe_create_shadow_experiment(
                conn,
                candidate={"parameter": "stop_loss", "from_value": -10.0, "to_value": -15.0},
                ready_for_shadow=True,
            )
            self.assertEqual(exp1["id"], exp2["id"])

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()


class SurgeonTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_trades(self, conn, n: int = 20) -> None:
        for i in range(n):
            pnl = 3.0 if i % 3 != 0 else -5.0
            exit_reason = "TRAILING_STOP" if pnl > 0 else "STOP_LOSS"
            entry_price = 0.37 + (i % 7) * 0.01
            conn.execute(
                """
                INSERT INTO early_reversion_v2_trades (
                    market_slug, window_start_ts, end_ts, side, strategy_name,
                    status, entry_price, entry_ts, exit_price, exit_reason,
                    pnl_percent, pnl_usdc, holding_time_seconds, created_at, closed_at
                ) VALUES (?, ?, ?, 'YES', 'NO_C', 'closed', ?, ?, ?, ?, ?, 0.1, ?, datetime('now'), datetime('now'))
                """,
                (
                    f"btc-{i}",
                    1000 + i * 300,
                    1000 + i * 300 + 300,
                    entry_price,
                    1000 + i * 300 + 10,
                    entry_price + pnl / 100,
                    exit_reason,
                    pnl,
                    30 + i,
                ),
            )

    def test_surgeon_produces_recommendation(self) -> None:
        from bot.evolution.surgeon import render_surgeon_block, run_surgeon

        with connect(self.db_path) as conn:
            self._seed_trades(conn, 30)
            conn.commit()
            result = run_surgeon(conn)

        self.assertGreater(result["sample_size"], 0)
        self.assertIn("recommendation", result)
        self.assertIn("q1_fresh_start", result)
        self.assertIn("q6_next_shadow", result)
        block = render_surgeon_block(result)
        self.assertIn("STRATEGY SURGEON", block)
        self.assertIn("RECOMMENDATION", block)

    def test_surgeon_empty_db(self) -> None:
        from bot.evolution.surgeon import run_surgeon

        with connect(self.db_path) as conn:
            result = run_surgeon(conn)

        self.assertEqual(result["sample_size"], 0)
        self.assertIn("recommendation", result)


class CouncilTestCase(unittest.TestCase):
    """Tests for Trading Decision Council v1."""

    def _full_sources(self, *, trades_since: int = 400) -> dict:
        return {
            "optimizer": {
                "parameter_optimizer": {
                    "current": {"entry": 0.40, "stop_pct": -10.0, "trailing_activation": 0.03},
                    "optimal": {"entry": 0.39, "stop_pct": -10.0, "trailing_activation": 0.03},
                    "expected_improvement_pct": 14.0,
                },
                "walk_forward": {"trend": "stable", "rows": [{"generalizes": True}]},
                "overfit_detector": {"level": "LOW"},
            },
            "strategy_review": {
                "final_verdict": {
                    "change_parameter": "entry",
                    "to_value": 0.39,
                    "confidence_pct": 92,
                    "reason": "Entry threshold reduction supported by data",
                }
            },
            "scientist": {
                "best_next_step": {
                    "blocked": False,
                    "recommendation": "Lower entry threshold to 0.39",
                    "suggested_value": 0.39,
                    "expected_pf_pct": 14.0,
                    "confidence_pct": 88,
                }
            },
            "trading_brain": {
                "top_knowledge": [{"feature": "entry_threshold", "direction": "negative", "importance": 0.8}]
            },
            "ai_agent": {
                "decisions": {"ALLOW": 15, "SKIP": 5},
                "patterns": {"top_improvement_signal": {"parameter": "entry", "value": 0.39, "confidence": 75}},
            },
            "live_sample": {"current_since_change": trades_since, "total_trades": 742},
        }

    def test_council_produces_ready_for_shadow_with_consensus(self) -> None:
        from bot.evolution.council import convene_council

        sources = self._full_sources()
        surgeon = {"recommendation": {"parameter": "entry", "to_value": 0.36, "direction": "lower", "reason": "PF weak at 0.40"}}
        result = convene_council(sources, surgeon=surgeon)

        self.assertEqual(result.status, EvolutionStatus.READY_FOR_SHADOW.value)
        self.assertEqual(result.final_parameter, "entry")
        self.assertIsNotNone(result.final_to_value)
        self.assertEqual(len(result.votes), 6)
        self.assertGreater(result.confidence_pct, 0)

    def test_council_returns_keep_with_insufficient_trades(self) -> None:
        from bot.evolution.council import convene_council

        sources = self._full_sources(trades_since=50)
        result = convene_council(sources)

        self.assertEqual(result.status, EvolutionStatus.KEEP.value)
        self.assertEqual(len(result.votes), 6)

    def test_council_returns_keep_when_no_consensus(self) -> None:
        from bot.evolution.council import convene_council

        sources = {
            "optimizer": {
                "parameter_optimizer": {"current": {}, "optimal": {}},
                "walk_forward": {"trend": "stable"},
                "overfit_detector": {"level": "LOW"},
            },
            "strategy_review": {"final_verdict": {}},
            "scientist": {"best_next_step": {"blocked": True}},
            "trading_brain": {},
            "ai_agent": {"decisions": {"ALLOW": 5, "SKIP": 10}},
            "live_sample": {"current_since_change": 400, "total_trades": 742},
        }
        result = convene_council(sources)
        self.assertEqual(result.status, EvolutionStatus.KEEP.value)

    def test_council_votes_are_labeled(self) -> None:
        from bot.evolution.council import convene_council

        sources = self._full_sources()
        surgeon = {"recommendation": {"parameter": "entry", "to_value": 0.36}}
        result = convene_council(sources, surgeon=surgeon)

        sources_in_votes = [v.source for v in result.votes]
        self.assertIn("Optimizer", sources_in_votes)
        self.assertIn("Scientist", sources_in_votes)
        self.assertIn("Brain", sources_in_votes)
        self.assertIn("Strategy Review", sources_in_votes)
        self.assertIn("Surgeon", sources_in_votes)
        self.assertIn("AI Agent", sources_in_votes)

        for v in result.votes:
            self.assertTrue(len(v.label) > 0)

    def test_council_with_shadow_running(self) -> None:
        from bot.evolution.council import convene_council

        sources = self._full_sources()
        shadow = {
            "status": "RUNNING",
            "parameter": "entry_threshold",
            "current_value": 0.40,
            "shadow_value": 0.39,
            "sample_size": 37,
            "target_sample_size": 200,
            "reason": "Shadow in progress",
        }
        result = convene_council(sources, shadow_state=shadow)
        self.assertEqual(result.status, EvolutionStatus.SHADOW_RUNNING.value)
        self.assertEqual(result.final_to_value, 0.39)

    def test_render_council_block(self) -> None:
        from bot.evolution.council import convene_council
        from bot.evolution.render import render_council_block

        sources = self._full_sources()
        surgeon = {"recommendation": {"parameter": "entry", "to_value": 0.36}}
        result = convene_council(sources, surgeon=surgeon)
        evolution = {"council": {
            "status": result.status,
            "final_parameter": result.final_parameter,
            "final_from_value": result.final_from_value,
            "final_to_value": result.final_to_value,
            "final_label": result.final_label,
            "confidence_pct": result.confidence_pct,
            "reason": result.reason,
            "votes": [
                {"source": v.source, "label": v.label, "parameter": v.parameter, "value": v.value, "is_keep": v.is_keep, "reason": v.reason}
                for v in result.votes
            ],
            "evidence_trades": result.evidence_trades,
            "expected_pf_pct": result.expected_pf_pct,
        }}
        block = render_council_block(evolution)
        self.assertIn("DECISION COUNCIL", block)
        self.assertIn("Optimizer", block)
        self.assertIn("Scientist", block)
        self.assertIn("Brain", block)
        self.assertIn("Surgeon", block)
        self.assertIn("Final", block)


class RegimeShadowTestCase(unittest.TestCase):
    """Tests for Regime Shadow — counterfactual filter experiment."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_trades_with_features(self, conn, n: int = 20) -> None:
        regimes = ["Strong Uptrend", "Mean Reversion", "Range", "News Spike", "Compression"]
        for i in range(n):
            pnl = 3.0 if i % 3 != 0 else -5.0
            entry_price = 0.38
            conn.execute(
                """
                INSERT INTO early_reversion_v2_trades (
                    market_slug, window_start_ts, end_ts, side, strategy_name,
                    status, entry_price, entry_ts, exit_price, exit_reason,
                    pnl_percent, pnl_usdc, holding_time_seconds, created_at, closed_at
                ) VALUES (?, ?, ?, 'YES', 'NO_C', 'closed', ?, ?, ?, ?, ?, 0.1, ?, datetime('now'), datetime('now'))
                """,
                (
                    f"btc-regime-{i}",
                    1000 + i * 300,
                    1000 + i * 300 + 300,
                    entry_price,
                    1000 + i * 300 + 10,
                    entry_price + pnl / 100,
                    "TRAILING_STOP" if pnl > 0 else "STOP_LOSS",
                    pnl,
                    30 + i,
                ),
            )
            trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            regime = regimes[i % len(regimes)]
            conn.execute(
                """
                INSERT INTO trade_features (
                    trade_id, source_table, market_slug, strategy_name, side,
                    entry_ts, entry_price, regime_label
                ) VALUES (?, 'early_reversion_v2_trades', ?, 'NO_C', 'YES', ?, ?, ?)
                """,
                (trade_id, f"btc-regime-{i}", 1000 + i * 300 + 10, entry_price, regime),
            )

    def test_create_regime_shadow(self) -> None:
        from bot.evolution.regime_shadow import create_regime_shadow, get_running_regime_shadow

        with connect(self.db_path) as conn:
            exp = create_regime_shadow(
                conn,
                filter_name="BTC Uptrend Filter",
                regimes=("Strong Uptrend", "News Spike"),
            )
            conn.commit()
            running = get_running_regime_shadow(conn)

        self.assertIsNotNone(running)
        self.assertEqual(exp["filter_name"], "BTC Uptrend Filter")
        self.assertEqual(exp["status"], "RUNNING")

    def test_sync_evaluates_trades(self) -> None:
        from bot.evolution.regime_shadow import create_regime_shadow, sync_regime_shadow

        with connect(self.db_path) as conn:
            exp = create_regime_shadow(
                conn,
                filter_name="BTC Uptrend Filter",
                regimes=("Strong Uptrend", "News Spike"),
            )
            conn.commit()
            self._seed_trades_with_features(conn, 20)
            conn.commit()
            result = sync_regime_shadow(conn)
            conn.commit()

        self.assertIsNotNone(result)
        self.assertGreater(int(result["sample_size"]), 0)
        self.assertGreater(int(result["skipped"]), 0)

    def test_saved_loss_and_missed_profit(self) -> None:
        from bot.evolution.regime_shadow import (
            create_regime_shadow,
            regime_shadow_state,
            sync_regime_shadow,
        )

        with connect(self.db_path) as conn:
            create_regime_shadow(
                conn,
                filter_name="BTC Uptrend Filter",
                regimes=("Strong Uptrend", "News Spike"),
            )
            conn.commit()
            self._seed_trades_with_features(conn, 20)
            conn.commit()
            sync_regime_shadow(conn)
            conn.commit()
            state = regime_shadow_state(conn)

        self.assertIsNotNone(state)
        self.assertGreaterEqual(state["saved_losses"], 0)
        self.assertGreaterEqual(state["missed_winners"], 0)
        self.assertEqual(state["skipped"], state["saved_losses"] + state["missed_winners"])

    def test_render_regime_shadow_block(self) -> None:
        from bot.evolution.regime_shadow import render_regime_shadow_block

        state = {
            "filter_name": "BTC Uptrend Filter",
            "regimes": ["Strong Uptrend", "News Spike"],
            "sample_size": 100,
            "target_sample_size": 200,
            "skipped": 47,
            "saved_losses": 31,
            "missed_winners": 16,
            "net_pf_improvement_pct": 18.0,
            "status": "RUNNING",
            "verdict": None,
        }
        block = render_regime_shadow_block(state)
        self.assertIn("REGIME SHADOW", block)
        self.assertIn("Skipped: 47", block)
        self.assertIn("Saved losses: 31", block)
        self.assertIn("Missed winners: 16", block)
        self.assertIn("+18%", block)

    def test_never_two_regime_shadows(self) -> None:
        from bot.evolution.regime_shadow import create_regime_shadow

        with connect(self.db_path) as conn:
            exp1 = create_regime_shadow(
                conn, filter_name="Filter A", regimes=("Strong Uptrend",)
            )
            exp2 = create_regime_shadow(
                conn, filter_name="Filter B", regimes=("Panic",)
            )
        self.assertEqual(exp1["id"], exp2["id"])


class CouncilSurgeonInterfaceTestCase(unittest.TestCase):
    """Regression tests for Council <-> Surgeon interface mismatch."""

    def _sources(self) -> dict:
        return {
            "optimizer": {
                "parameter_optimizer": {
                    "current": {"entry": 0.40},
                    "optimal": {"entry": 0.39},
                    "expected_improvement_pct": 14.0,
                },
                "walk_forward": {"trend": "stable", "rows": [{"generalizes": True}]},
                "overfit_detector": {"level": "LOW"},
            },
            "strategy_review": {"final_verdict": {}},
            "scientist": {"best_next_step": {"blocked": True}},
            "trading_brain": {},
            "ai_agent": {"decisions": {"ALLOW": 10, "SKIP": 5}},
            "live_sample": {"current_since_change": 400, "total_trades": 742},
        }

    def test_surgeon_string_recommendation_does_not_crash(self) -> None:
        from bot.evolution.council import convene_council

        surgeon = {"recommendation": "Lower entry to 0.39 for PF improvement"}
        result = convene_council(self._sources(), surgeon=surgeon)
        self.assertIsNotNone(result)
        surgeon_vote = next(v for v in result.votes if v.source == "Surgeon")
        self.assertEqual(surgeon_vote.parameter, "entry")
        self.assertEqual(surgeon_vote.value, 0.39)

    def test_surgeon_dict_recommendation_works(self) -> None:
        from bot.evolution.council import convene_council

        surgeon = {
            "recommendation": {
                "parameter": "entry",
                "value": 0.36,
                "to_value": 0.36,
                "direction": "lower",
                "decision": "CHANGE",
                "reason": "PF weak at 0.40",
                "confidence": 87,
            }
        }
        result = convene_council(self._sources(), surgeon=surgeon)
        surgeon_vote = next(v for v in result.votes if v.source == "Surgeon")
        self.assertEqual(surgeon_vote.parameter, "entry")
        self.assertEqual(surgeon_vote.value, 0.36)
        self.assertEqual(surgeon_vote.confidence, 87)

    def test_surgeon_keep_recommendation(self) -> None:
        from bot.evolution.council import convene_council

        surgeon = {
            "recommendation": {
                "parameter": None,
                "value": None,
                "decision": "KEEP",
                "reason": "No clear improvement",
                "confidence": 0,
            }
        }
        result = convene_council(self._sources(), surgeon=surgeon)
        surgeon_vote = next(v for v in result.votes if v.source == "Surgeon")
        self.assertTrue(surgeon_vote.is_keep)
        self.assertIn("No clear improvement", surgeon_vote.reason)

    def test_surgeon_none_recommendation(self) -> None:
        from bot.evolution.council import convene_council

        surgeon = {"recommendation": None}
        result = convene_council(self._sources(), surgeon=surgeon)
        surgeon_vote = next(v for v in result.votes if v.source == "Surgeon")
        self.assertTrue(surgeon_vote.is_keep)

    def test_surgeon_invalid_recommendation_type(self) -> None:
        from bot.evolution.council import convene_council

        surgeon = {"recommendation": 42}
        result = convene_council(self._sources(), surgeon=surgeon)
        surgeon_vote = next(v for v in result.votes if v.source == "Surgeon")
        self.assertTrue(surgeon_vote.is_keep)

    def test_surgeon_empty_dict(self) -> None:
        from bot.evolution.council import convene_council

        surgeon = {}
        result = convene_council(self._sources(), surgeon=surgeon)
        surgeon_vote = next(v for v in result.votes if v.source == "Surgeon")
        self.assertTrue(surgeon_vote.is_keep)

    def test_surgeon_string_keep(self) -> None:
        from bot.evolution.council import convene_council

        surgeon = {"recommendation": "Insufficient data — no closed trades."}
        result = convene_council(self._sources(), surgeon=surgeon)
        surgeon_vote = next(v for v in result.votes if v.source == "Surgeon")
        self.assertTrue(surgeon_vote.is_keep)

    def test_render_works_with_structured_recommendation(self) -> None:
        from bot.evolution.surgeon import render_surgeon_block

        surgeon = {
            "sample_size": 500,
            "metrics": {"pf": 1.65, "wr": 58.0, "dd": 4.1},
            "q1_fresh_start": "Focus entries at 0.37",
            "q2_biggest_loss": {"exit_reason": "STOP_LOSS", "exit_reason_loss": 45.0, "worst_entry_price": 0.40, "worst_entry_pf": 0.91},
            "q3_biggest_profit": {"exit_reason": "TRAILING_STOP", "exit_reason_profit": 120.0, "best_entry_price": 0.37, "best_entry_pf": 2.1},
            "q4_hurts_pf": {"parameter": "entry_threshold", "value": 0.40, "pf": 0.91},
            "q5_helps_pf": {"parameter": "entry_threshold", "value": 0.37, "pf": 2.1},
            "q6_next_shadow": {"parameter": "entry_threshold", "action": "Lower entry", "reason": "PF improvement"},
            "recommendation": {
                "parameter": "entry",
                "value": 0.37,
                "decision": "CHANGE",
                "reason": "PF improvement at lower entry",
                "confidence": 87,
            },
            "detail": {"baseline_pf": 1.65, "entry_pfs": {}, "exit_pfs": {}},
        }
        block = render_surgeon_block(surgeon)
        self.assertIn("RECOMMENDATION:", block)
        self.assertIn("entry", block)
        self.assertNotIn("AttributeError", block)


class EvolutionHistoryTestCase(unittest.TestCase):
    """Tests for Evolution History timeline."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_record_and_load_history(self) -> None:
        from bot.evolution.history import load_history, record_experiment_start

        with connect(self.db_path) as conn:
            v1 = record_experiment_start(
                conn,
                experiment_type="parameter",
                parameter="entry_threshold",
                from_value="0.40",
                to_value="0.39",
                description="entry_threshold 0.40 → 0.39",
                shadow_id=1,
            )
            conn.commit()
            history = load_history(conn)

        self.assertEqual(v1, 1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["parameter"], "entry_threshold")
        self.assertEqual(history[0]["status"], "RUNNING")

    def test_complete_updates_status(self) -> None:
        from bot.evolution.history import (
            load_history,
            record_experiment_complete,
            record_experiment_start,
        )

        with connect(self.db_path) as conn:
            record_experiment_start(
                conn,
                experiment_type="parameter",
                parameter="entry_threshold",
                from_value="0.40",
                to_value="0.39",
                description="entry_threshold 0.40 → 0.39",
                shadow_id=1,
            )
            record_experiment_complete(
                conn,
                shadow_id=1,
                experiment_type="parameter",
                verdict="PROMOTE",
                metrics={"live_pf": 1.5, "shadow_pf": 1.8},
            )
            conn.commit()
            history = load_history(conn)

        self.assertEqual(history[0]["status"], "PROMOTED")

    def test_render_history_block(self) -> None:
        from bot.evolution.history import render_history_block

        history = [
            {
                "version": 1,
                "experiment_type": "parameter",
                "parameter": "Entry",
                "from_value": "0.40",
                "to_value": "0.39",
                "status": "REJECTED",
                "description": "Entry 0.40 → 0.39",
                "started_at": "2026-06-01",
                "completed_at": "2026-06-15",
            },
            {
                "version": 2,
                "experiment_type": "regime_filter",
                "parameter": "BTC Filter",
                "from_value": None,
                "to_value": None,
                "status": "RUNNING",
                "description": "Filter: Strong Uptrend",
                "started_at": "2026-06-16",
                "completed_at": None,
            },
        ]
        block = render_history_block(history)
        self.assertIn("EVOLUTION HISTORY", block)
        self.assertIn("Version 1", block)
        self.assertIn("Version 2", block)
        self.assertIn("Rejected", block)
        self.assertIn("Shadow", block)

    def test_version_increments(self) -> None:
        from bot.evolution.history import load_history, record_experiment_start

        with connect(self.db_path) as conn:
            record_experiment_start(
                conn,
                experiment_type="parameter",
                parameter="entry",
                description="test 1",
                shadow_id=1,
            )
            record_experiment_start(
                conn,
                experiment_type="regime_filter",
                parameter="btc_filter",
                description="test 2",
                shadow_id=2,
            )
            conn.commit()
            history = load_history(conn)

        self.assertEqual(history[0]["version"], 1)
        self.assertEqual(history[1]["version"], 2)


if __name__ == "__main__":
    unittest.main()
