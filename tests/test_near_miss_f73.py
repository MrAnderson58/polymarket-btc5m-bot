"""Phase F.7.3 — near miss diagnostics tests."""

from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.near_miss_f73 import (
    CAT_CONFIDENCE,
    CAT_MARKET_SCORE,
    build_rejection_details,
    record_near_miss_f73,
)
from bot.research.market_events.signal_intelligence.reports_f73 import (
    detector_stats_report,
    diagnostics_dashboard_stats,
    near_miss_report,
    threshold_report,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_event


def _seed_f2_f5_f7(
    conn,
    event_id: int,
    *,
    dynamic_conf: float = 6.8,
    market_score: float = 52.0,
    final_conf: float = 6.8,
    telegram_eligible: int = 0,
) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_events_signal_reports_f2 (
          event_id, exchange_consensus, exchange_detail_json, funding_regime, oi_regime,
          rvol_20, rvol_100, vwap_deviation_pct, volume_label, market_structure_json,
          market_structure_labels, atr_percentile, atr_expansion, atr_exhaustion,
          correlation_snapshot_json, correlation_verdict, historical_count,
          historical_reversal_count, historical_reversal_rate, historical_similarity_json,
          confidence_score, confidence_breakdown_json, reversal_probability,
          entry_recommendation, expected_target_pct, expected_stop_pct,
          ai_summary_v2_ru, telegram_rendered, prompt_version, created_at
        ) VALUES (?, 'aligned', '[]', 'unknown', 'flat', 1.2, 1.0, 0.1, 'low',
          '{}', '[]', 50, 1.0, 0, '{}', 'neutral', 5, 3, 0.6, '[]',
          7.0, '{}', 0.7, 'wait', 2.0, 1.0, 'test', 'test', 'f2_test', ?)
        """,
        (event_id, now),
    )
    conn.execute(
        """
        INSERT INTO market_events_signal_reports_f5 (
          event_id, dynamic_confidence, reversal_probability, continuation_probability,
          entry_quality, signal_cause, interest_factors_json, historical_examples_json,
          risk_reward, tp_probabilities_json, tp1_pct, tp2_pct, tp3_pct, stop_pct,
          priority_score, telegram_eligible, telegram_skip_reason, ai_summary_ru,
          telegram_rendered, created_at
        ) VALUES (?, ?, 0.7, 0.3, 'B', 'test', '[]', '[]', 2.5, '{}',
          1.0, 2.0, 3.0, 1.5, 80, ?, 'low_confidence', 'test', 'test', ?)
        """,
        (event_id, dynamic_conf, telegram_eligible, now),
    )
    conn.execute(
        """
        INSERT INTO market_events_market_intelligence_f7 (
          event_id, market_score, component_scores_json, final_confidence,
          success_probability, liquidation_regime, liquidation_intel_json,
          dominance_regime, dominance_json, whale_score, whale_intel_json,
          news_impact, news_keywords_json, long_trend_stage, long_trend_json,
          image_intel_json, interest_factors_json, telegram_rendered, created_at
        ) VALUES (?, ?, '{}', ?, 0.7, 'neutral', '{}', 'RISK ON', '{}',
          0, '{}', 'LOW', '[]', NULL, '{}', '{}', '[]', 'test', ?)
        """,
        (event_id, market_score, final_conf, now),
    )


class NearMissF73Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {"ME_F73_NEAR_MISS": "true"}, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_schema_v23(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v23", applied)
            self.assertEqual(SCHEMA_VERSION, 31)

    def test_rejection_reason_not_generic(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="SOL", ret=-3.0)
            _seed_f2_f5_f7(conn, eid, dynamic_conf=6.8, market_score=52.0, final_conf=6.8)
            reason, category, missing = build_rejection_details(
                conn, event_id=eid, skip_code="low_final_confidence",
            )
            self.assertIn("Confidence 6.8 < 7.5", reason)
            self.assertIn("Market Score 52 < 55", reason)
            self.assertEqual(category, CAT_CONFIDENCE)
            self.assertNotEqual(reason, "Rejected")

    def test_record_near_miss(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="ETH", ret=-2.5)
            _seed_f2_f5_f7(conn, eid)
            ok = record_near_miss_f73(conn, event_id=eid, skip_code="low_final_confidence")
            self.assertTrue(ok)
            row = conn.execute(
                "SELECT rejection_reason, rejection_category, detector, missing_conditions_json "
                "FROM market_events_near_miss_f73 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["detector"], "SHOCK_A")
            self.assertEqual(row["rejection_category"], CAT_CONFIDENCE)
            self.assertIn("Confidence", row["rejection_reason"])
            missing = json.loads(row["missing_conditions_json"] or "{}")
            self.assertIn("confidence", missing)

    def test_near_miss_report(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            _seed_f2_f5_f7(conn, eid, market_score=40.0, final_conf=6.0)
            record_near_miss_f73(conn, event_id=eid, skip_code="low_market_score")
            text = near_miss_report(conn, days=1)
            self.assertIn("Rejected", text)
            self.assertIn("Market Score", text)

    def test_detector_stats_report(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            _seed_f2_f5_f7(conn, eid)
            text = detector_stats_report(conn, days=1)
            self.assertIn("SHOCK_A", text)
            self.assertIn("detected", text)

    def test_threshold_report(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            _seed_f2_f5_f7(conn, eid, final_conf=7.2)
            text = threshold_report(conn, days=30)
            self.assertIn("Current Telegram threshold", text)
            self.assertIn("7.5", text)

    def test_dashboard_stats(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            _seed_f2_f5_f7(conn, eid)
            record_near_miss_f73(conn, event_id=eid, skip_code="low_confidence")
            stats = diagnostics_dashboard_stats(conn)
            self.assertGreaterEqual(stats["rejected_today"], 1)
            self.assertTrue(stats["top_rejection_reasons"])
