"""Tests for Research Hypothesis Engine V1."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.hypothesis_engine.generate import (
    generate_hypothesis_candidates,
    score_hypothesis,
)
from bot.research.market_events.hypothesis_engine.report import (
    format_hypothesis_detail,
    format_hypothesis_show,
    write_hypotheses_report,
)
from bot.research.market_events.hypothesis_engine.schema import (
    STATUS_ARCHIVED,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_TESTING,
    STATUS_VALIDATED,
)
from bot.research.market_events.hypothesis_engine.store import (
    load_hypothesis_bundle,
    list_hypotheses,
)
from bot.research.market_events.hypothesis_engine.validate import (
    decide_status,
    run_hypothesis_validate,
    validate_candidate,
)
from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema


class HypothesisEngineV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "hyp.db"
        configure_unit_test_db_isolation(self.db_path)
        self.patterns_root = Path(self._tmpdir.name) / "research"
        self.patterns_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_knowledge(self, conn) -> None:
        ensure_knowledge_engine_schema(conn)
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO knowledge_features (
              feature_name, feature_key, ev_delta, pf_delta, wr_delta,
              stability_score, sample_size, confidence, verdict, rule_text, last_updated
            ) VALUES
              ('Funding', 'funding', 0.6242, 0.2, 5.0, 1.0, 94, 0.8, 'KEEP', 'Funding > median', ?),
              ('Trend', 'trend', 0.5100, 0.15, 4.0, 1.0, 94, 0.75, 'KEEP', 'Trend > median', ?),
              ('OI Δ', 'oi_delta', 0.0500, 0.01, 1.0, 0.5, 94, 0.3, 'WATCH', 'OI Δ ≤ median', ?),
              ('AI Score', 'ai_score', 0.5000, 0.14, 4.0, 0.8, 90, 0.7, 'KEEP', 'AI Score > median', ?),
              ('Volatility', 'volatility', -0.2000, -0.05, -2.0, 0.4, 80, 0.4, 'WATCH', 'Volatility ≤ median', ?)
            """,
            (now, now, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO knowledge_rules (
              rule_key, feature_name, rule_text, ev_delta, pf_delta, wr_delta,
              sample_size, confidence, status, last_updated
            ) VALUES
              ('funding::Funding > median', 'Funding', 'Funding > median', 0.6242, 0.2, 5.0, 94, 80.0, 'validated', ?),
              ('vol::Direction = SHORT & low vol', 'Volatility', 'Direction SHORT works only at low Volatility', -0.1, 0.0, 0.0, 40, 55.0, 'candidate', ?),
              ('trend::Trend > median', 'Trend', 'Trend > median', 0.51, 0.15, 4.0, 94, 75.0, 'validated', ?)
            """,
            (now, now, now),
        )
        conn.execute(
            """
            INSERT INTO knowledge_interactions (
              pair_key, feature_a, feature_b, synergy, ev_joint, ev_a, ev_b,
              sample_size, stronger_together, last_updated
            ) VALUES
              ('funding|trend', 'Funding', 'Trend', 0.6238, 1.2, 0.62, 0.51, 94, 1, ?),
              ('oi_delta|funding', 'OI Δ', 'Funding', 0.40, 0.9, 0.05, 0.62, 70, 1, ?)
            """,
            (now, now),
        )
        conn.commit()

    def _seed_patterns(self) -> None:
        payload = {
            "version": 1,
            "n_trades": 200,
            "k": 8,
            "clusters": [
                {
                    "cluster_id": 7,
                    "name": "Momentum Continuation (LONG)",
                    "features": {"funding": 0.02, "trend": 0.8, "volatility": 0.4, "oi": 1.0},
                    "metrics": {"n": 94, "ev": 1.2, "wr": 62.0, "rank_score": 5.4},
                },
                {
                    "cluster_id": 2,
                    "name": "Liquidity Sweep Risk",
                    "features": {"funding": -0.01, "trend": -0.5},
                    "metrics": {"n": 40, "ev": -1.5, "wr": 35.0, "rank_score": -5.5},
                },
            ],
        }
        (self.patterns_root / "patterns.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_score_and_status(self) -> None:
        evidence = [
            {"source_type": "FeatureValidation", "source_name": "Funding", "reference": "ΔEV=0.6", "weight": 1},
            {"source_type": "Interaction", "source_name": "Funding×Trend", "reference": "synergy=0.6", "weight": 2},
            {"source_type": "PatternDiscovery", "source_name": "Cluster_7", "reference": "n=94", "weight": 1},
        ]
        scores = score_hypothesis(evidence=evidence, sample_size=94, effect_magnitude=0.62)
        self.assertGreaterEqual(scores["evidence_score"], 3)
        self.assertGreaterEqual(scores["confidence"], 50)
        status = decide_status(
            evidence_score=scores["evidence_score"],
            confidence=scores["confidence"],
            sample_size=94,
            effect_magnitude=0.62,
        )
        self.assertIn(status, (STATUS_TESTING, STATUS_VALIDATED))
        self.assertEqual(
            decide_status(
                evidence_score=0,
                confidence=10,
                sample_size=100,
                effect_magnitude=0.5,
            ),
            STATUS_REJECTED,
        )
        self.assertEqual(
            decide_status(
                evidence_score=1,
                confidence=30,
                sample_size=20,
                effect_magnitude=0.2,
            ),
            STATUS_NEW,
        )
        self.assertEqual(
            decide_status(
                evidence_score=2,
                confidence=80,
                sample_size=42,
                effect_magnitude=0.0,
            ),
            STATUS_NEW,
        )

    def test_generate_validate_and_knowledge_history(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_knowledge(conn)
            self._seed_patterns()

            cands = generate_hypothesis_candidates(conn, patterns_root=self.patterns_root)
            self.assertTrue(cands)
            types = {c["generated_from"] for c in cands}
            self.assertIn("Strong Interaction", types)
            self.assertIn("Feature Dependency", types)
            self.assertIn("Pattern Hypothesis", types)
            self.assertIn("Conditional Feature", types)
            self.assertIn("Replacement", types)
            for c in cands:
                self.assertTrue(c["evidence"], msg=c["title"])

            stats = run_hypothesis_validate(conn, patterns_root=self.patterns_root)
            self.assertGreater(stats["upserted"], 0)
            bundle = load_hypothesis_bundle(conn)
            self.assertTrue(bundle)
            top = bundle[0]
            self.assertTrue(top["evidence"])
            detail = format_hypothesis_detail(top)
            self.assertIn("Основание", detail)
            self.assertIn("Evidence Score", detail)

            # Re-validate updates last_checked / history
            before = list_hypotheses(conn)
            stats2 = run_hypothesis_validate(conn, patterns_root=self.patterns_root)
            self.assertEqual(stats2["upserted"], stats["upserted"])
            after = list_hypotheses(conn)
            self.assertEqual(len(before), len(after))

            hist_n = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM knowledge_history WHERE entity_type='hypothesis'"
                ).fetchone()["n"]
            )
            self.assertGreater(hist_n, 0)

            text = format_hypothesis_show(conn)
            self.assertIn("TOP HYPOTHESES", text)
            path = write_hypotheses_report(conn, root=self.patterns_root)
            self.assertTrue(path.exists())
            md = path.read_text(encoding="utf-8")
            self.assertIn("Validated Hypotheses", md)
            self.assertIn("Top Opportunities", md)

    def test_status_change_on_validate(self) -> None:
        cand = {
            "hypothesis_key": "t1",
            "title": "test",
            "description": "d",
            "generated_from": "Strong Interaction",
            "sample_size": 100,
            "effect_magnitude": 0.8,
            "evidence": [
                {"source_type": "FeatureValidation", "source_name": "A", "reference": "x", "weight": 1},
                {"source_type": "Interaction", "source_name": "A×B", "reference": "y", "weight": 2},
                {"source_type": "PatternDiscovery", "source_name": "C1", "reference": "z", "weight": 1},
            ],
        }
        v = validate_candidate(cand, previous_status=STATUS_NEW)
        self.assertIn(v["status"], (STATUS_TESTING, STATUS_VALIDATED))
        weak = validate_candidate(
            {
                **cand,
                "sample_size": 2,
                "evidence": [],
                "effect_magnitude": 0.0,
            },
            previous_status=STATUS_TESTING,
        )
        self.assertEqual(weak["status"], STATUS_REJECTED)
        archived = validate_candidate(cand, previous_status=STATUS_ARCHIVED)
        self.assertEqual(archived["status"], STATUS_ARCHIVED)


if __name__ == "__main__":
    unittest.main()
