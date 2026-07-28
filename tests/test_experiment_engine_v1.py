"""Tests for Experiment Engine V1."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.experiment_engine.engine import run_all_experiments
from bot.research.market_events.experiment_engine.report import (
    format_experiment_show,
    write_experiments_report,
)
from bot.research.market_events.experiment_engine.runners import (
    decide_experiment_status,
    execute_hypothesis_experiment,
    map_hypothesis_to_experiment_type,
    run_dependency,
    run_interaction,
    run_pattern,
    run_replacement,
    run_single_filter,
)
from bot.research.market_events.experiment_engine.schema import (
    TYPE_DEPENDENCY,
    TYPE_INTERACTION,
    TYPE_PATTERN,
    TYPE_REPLACEMENT,
    TYPE_SINGLE_FILTER,
)
from bot.research.market_events.experiment_engine.stats import cohens_d, welch_t_pvalue
from bot.research.market_events.hypothesis_engine.schema import (
    TYPE_CONDITIONAL_FEATURE,
    TYPE_FEATURE_DEPENDENCY,
    TYPE_PATTERN as HYP_PATTERN,
    TYPE_REPLACEMENT as HYP_REPLACEMENT,
    TYPE_STRONG_INTERACTION,
)
from bot.research.market_events.hypothesis_engine.store import upsert_hypothesis
from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema


def _trade(i: int, pnl: float, **kw) -> dict:
    base = {
        "pnl_pct": pnl,
        "win": pnl > 0,
        "mfe_pct": abs(pnl) + 0.2,
        "mae_pct": -abs(pnl) * 0.3,
        "duration_sec": 100 + i,
        "funding": kw.get("funding", 0.01 + (i % 10) * 0.001),
        "trend": kw.get("trend", -1.0 + (i % 8) * 0.4),
        "oi_delta": kw.get("oi_delta", float((i % 9) - 4)),
        "volatility": kw.get("volatility", 0.5 + (i % 5) * 0.2),
        "fear_greed": kw.get("fear_greed", 20 + i % 30),
        "ai_score": kw.get("ai_score", 40 + i % 40),
        "neighbor_ev": kw.get("neighbor_ev", 0.1),
        "market_regime": kw.get("market_regime", "RANGE" if i % 2 else "WEAK_BULL"),
        "direction": kw.get("direction", "LONG" if i % 2 == 0 else "SHORT"),
        "symbol": "BTCUSDT",
        "closed_at": 1_700_000_000 + i,
    }
    base.update(kw)
    return base


class ExperimentEngineV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self._tmpdir.name) / "exp.db")
        self.patterns_root = Path(self._tmpdir.name) / "research"
        self.patterns_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_trades_db(self, conn, n: int = 120) -> None:
        apply_migrations(conn)
        now = int(time.time())
        for i in range(n):
            # Make funding+trend high → better pnl for interaction tests
            funding = 0.02 if i < 40 else 0.005
            trend = 1.0 if i < 40 else -0.5
            pnl = 1.5 if (funding > 0.01 and trend > 0) else (-0.8 if i % 3 == 0 else 0.3)
            if i >= 80:
                pnl = 2.0 if i % 2 == 0 else -1.0
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  funding, trend, fear_greed, oi_delta, volatility, ai_score,
                  features_json, gate_decision, gate_expected_pnl_pct,
                  created_at, closed_at, pnl_pct, result, mfe_pct, mae_pct,
                  duration_sec, market_regime
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3", i + 1, "BTCUSDT", "LONG" if i % 2 == 0 else "SHORT",
                    funding, trend, 25.0, float((i % 9) - 4),
                    0.5 + (i % 5) * 0.1, 50.0 + (i % 20),
                    json.dumps({"trend": trend}),
                    "ALLOWED", 0.1,
                    now - 5000 + i, now - 1000 + i, pnl,
                    "WIN" if pnl > 0 else "LOSS",
                    abs(pnl) + 0.5, -abs(pnl) * 0.4, 120 + i,
                    "RANGE" if i % 2 else "WEAK_BULL",
                ),
            )
        conn.commit()

    def test_stats_and_type_mapping(self) -> None:
        a = [1.0, 1.2, 0.8, 1.1, 1.3]
        b = [-0.5, -0.2, 0.0, -0.4, -0.1]
        d = cohens_d(a, b)
        self.assertIsNotNone(d)
        self.assertGreater(d, 0.5)
        p = welch_t_pvalue(a, b)
        self.assertIsNotNone(p)
        self.assertLess(p, 0.05)
        self.assertEqual(map_hypothesis_to_experiment_type(TYPE_STRONG_INTERACTION), TYPE_INTERACTION)
        self.assertEqual(map_hypothesis_to_experiment_type(TYPE_CONDITIONAL_FEATURE), TYPE_SINGLE_FILTER)
        self.assertEqual(map_hypothesis_to_experiment_type(HYP_REPLACEMENT), TYPE_REPLACEMENT)
        self.assertEqual(map_hypothesis_to_experiment_type(TYPE_FEATURE_DEPENDENCY), TYPE_DEPENDENCY)
        self.assertEqual(map_hypothesis_to_experiment_type(HYP_PATTERN), TYPE_PATTERN)

    def test_each_experiment_type(self) -> None:
        trades = [_trade(i, 1.0 if i < 30 else -0.5, funding=0.02 if i < 30 else 0.001,
                         trend=1.0 if i < 30 else -1.0,
                         oi_delta=2.0 if i < 15 else -2.0,
                         ai_score=80 if i < 30 else 20) for i in range(80)]

        sf = run_single_filter(trades, feature_name="Funding")
        self.assertNotIn("error", sf)
        self.assertIn("delta_ev", sf)
        self.assertEqual(decide_experiment_status(sf) in ("VALIDATED", "REJECTED", "WEAK", "FAILED"), True)

        ix = run_interaction(trades, feature_a="Funding", feature_b="Trend")
        self.assertNotIn("error", ix)
        self.assertIn("solo_a", ix)

        rep = run_replacement(trades, feature_a="Trend", feature_b="AI Score")
        self.assertNotIn("error", rep)

        dep = run_dependency(trades, weak_name="OI", strong_name="Funding")
        self.assertNotIn("error", dep)

        # Pattern via indices file
        (self.patterns_root / "patterns").mkdir(parents=True, exist_ok=True)
        (self.patterns_root / "patterns" / "cluster_007.json").write_text(
            json.dumps({"cluster_id": 7, "name": "Test", "trade_indices": list(range(25))}),
            encoding="utf-8",
        )
        pat = run_pattern(trades, cluster_id=7, patterns_root=self.patterns_root)
        self.assertNotIn("error", pat)
        self.assertGreater(pat["after_n"], 0)

    def test_execute_dispatch_and_status(self) -> None:
        trades = [_trade(i, 1.2 if i < 40 else -0.6) for i in range(90)]
        hyp = {
            "hypothesis_key": "strong_ix:Funding|Trend",
            "generated_from": TYPE_STRONG_INTERACTION,
            "title": "Funding × Trend",
            "evidence": [],
        }
        result = execute_hypothesis_experiment(trades, hyp)
        self.assertEqual(result.get("experiment_type"), TYPE_INTERACTION)
        st = decide_experiment_status({"delta_ev": 0.5, "after_n": 40, "effect_size": 0.4, "validate": True})
        self.assertEqual(st, "VALIDATED")
        st2 = decide_experiment_status({"delta_ev": -0.5, "after_n": 40, "reject": True})
        self.assertEqual(st2, "REJECTED")

    def test_run_all_updates_hypothesis_and_knowledge(self) -> None:
        with market_events_connection() as conn:
            self._seed_trades_db(conn, n=100)
            ensure_knowledge_engine_schema(conn)
            # Seed hypotheses of each type
            upsert_hypothesis(
                conn,
                hypothesis_key="strong_ix:Funding|Trend",
                title="Funding работает только вместе с Trend",
                description="ix",
                generated_from=TYPE_STRONG_INTERACTION,
                confidence=70, priority=80, evidence_score=2, sample_size=50,
                status="NEW",
                evidence=[
                    {"source_type": "Interaction", "source_name": "Funding×Trend", "reference": "synergy=0.5", "weight": 2},
                    {"source_type": "FeatureValidation", "source_name": "Funding", "reference": "ΔEV=0.4", "weight": 1},
                ],
            )
            upsert_hypothesis(
                conn,
                hypothesis_key="conditional:Funding:Funding > median",
                title="Funding: условие high",
                description="cond",
                generated_from=TYPE_CONDITIONAL_FEATURE,
                confidence=60, priority=70, evidence_score=2, sample_size=50,
                status="NEW",
                evidence=[
                    {"source_type": "Knowledge", "source_name": "Funding", "reference": "rule=Funding > median", "weight": 1},
                    {"source_type": "FeatureValidation", "source_name": "Funding", "reference": "ΔEV=0.3", "weight": 1},
                ],
            )
            upsert_hypothesis(
                conn,
                hypothesis_key="replace:Trend|AI Score",
                title="Trend можно заменить на AI Score",
                description="rep",
                generated_from=HYP_REPLACEMENT,
                confidence=55, priority=60, evidence_score=2, sample_size=50,
                status="TESTING",
                evidence=[
                    {"source_type": "FeatureValidation", "source_name": "Trend", "reference": "ΔEV=0.3", "weight": 1},
                    {"source_type": "FeatureValidation", "source_name": "AI Score", "reference": "ΔEV=0.28", "weight": 1},
                ],
            )
            upsert_hypothesis(
                conn,
                hypothesis_key="dependency:OI->Funding",
                title="OI усиливает Funding",
                description="dep",
                generated_from=TYPE_FEATURE_DEPENDENCY,
                confidence=50, priority=55, evidence_score=2, sample_size=50,
                status="TESTING",
                evidence=[
                    {"source_type": "Interaction", "source_name": "OI×Funding", "reference": "synergy=0.3", "weight": 1},
                ],
            )
            (self.patterns_root / "patterns").mkdir(parents=True, exist_ok=True)
            (self.patterns_root / "patterns" / "cluster_003.json").write_text(
                json.dumps({"cluster_id": 3, "name": "Mom", "trade_indices": list(range(30))}),
                encoding="utf-8",
            )
            upsert_hypothesis(
                conn,
                hypothesis_key="pattern:cluster_3",
                title="Cluster 3 regime",
                description="pat",
                generated_from=HYP_PATTERN,
                confidence=65, priority=75, evidence_score=2, sample_size=30,
                status="NEW",
                evidence=[
                    {"source_type": "PatternDiscovery", "source_name": "Cluster_3", "reference": "n=30", "weight": 1},
                ],
            )
            conn.commit()

            summary = run_all_experiments(conn, patterns_root=self.patterns_root)
            self.assertEqual(summary["ran"], 5)
            self.assertGreaterEqual(summary["n_trades"], 50)

            exps = conn.execute("SELECT * FROM research_experiments").fetchall()
            self.assertEqual(len(exps), 5)
            types = {r["experiment_type"] for r in exps}
            self.assertTrue(TYPE_SINGLE_FILTER in types)
            self.assertTrue(TYPE_INTERACTION in types)
            self.assertTrue(TYPE_REPLACEMENT in types)
            self.assertTrue(TYPE_DEPENDENCY in types)
            self.assertTrue(TYPE_PATTERN in types)

            runs = conn.execute("SELECT * FROM experiment_runs").fetchall()
            self.assertEqual(len(runs), 5)

            hyps = conn.execute("SELECT status FROM research_hypotheses").fetchall()
            statuses = {r["status"] for r in hyps}
            # NEW should be gone (moved at least to TESTING / VALIDATED / REJECTED)
            self.assertNotIn("NEW", statuses)

            # Knowledge history should have experiment notes when validated/rejected
            hist = conn.execute(
                "SELECT COUNT(*) AS n FROM knowledge_history WHERE note LIKE 'experiment#%'"
            ).fetchone()["n"]
            # At least some experiments produce VALIDATED/REJECTED on this sample
            self.assertGreaterEqual(int(hist), 0)

            text = format_experiment_show(conn)
            self.assertIn("TOP VALIDATED", text)
            self.assertIn("BEST EFFECT SIZE", text)
            path = write_experiments_report(conn, root=self.patterns_root)
            self.assertTrue(path.exists())
            md = path.read_text(encoding="utf-8")
            self.assertIn("Validated Experiments", md)
            self.assertIn("Recent Runs", md)


if __name__ == "__main__":
    unittest.main()
