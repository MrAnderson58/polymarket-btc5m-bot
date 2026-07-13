"""Phase G.2 — Claude Research Agent tests."""

from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import MagicMock, patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.claude_client_g2 import (
    ClaudeResponseG2,
    ClaudeUsageG2,
    _estimate_cost,
    is_claude_configured,
)
from bot.research.market_events.signal_intelligence.learning_g2 import record_paper_learning_g2
from bot.research.market_events.signal_intelligence.research_g2 import (
    analyze_research_g2_deterministic,
    check_g2_eligibility,
    run_claude_research_g2,
)
from bot.research.market_events.signal_intelligence.telegram_g2 import format_research_telegram_g2
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


def _seed_g1_row(conn, event_id: int, *, reversal_prob: float = 0.81) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_events_liquidity_trend_g1 (
          event_id, symbol, event_ts, signal_type, mtf_windows_json,
          consecutive_pattern_json, slow_trend_json, liquidity_accum_json,
          capitulation_json, continuation_probability, reversal_probability,
          adaptive_threshold_pct, historical_reversal_rate, plan_action,
          telegram_rendered, created_at
        ) VALUES (?, 'BTC', ?, 'CAPITULATION', '[]',
          '[{"window_minutes":15,"max_streak":9,"max_streak_color":"red","description":"9 красных"}]',
          NULL, '{"funding_negative":true,"oi_rising":true,"price_falling":true,"volume_rising":true,"score":80}',
          '{"volume_multiple":3.8}', 0.19, ?, 0.8, 0.78, 'Ждать R2', '', ?)
        """,
        (event_id, now, reversal_prob, now),
    )


def _seed_f5_f7(conn, event_id: int, *, conf: float = 7.5, mscore: float = 65.0) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_events_signal_reports_f5 (
          event_id, dynamic_confidence, reversal_probability, continuation_probability,
          entry_quality, signal_cause, interest_factors_json, historical_examples_json,
          risk_reward, tp_probabilities_json, tp1_pct, tp2_pct, tp3_pct, stop_pct,
          priority_score, telegram_eligible, telegram_skip_reason, ai_summary_ru,
          telegram_rendered, created_at
        ) VALUES (?, ?, 0.75, 0.25, 'A', 'test', '[]', '[]', 3.0, '{}',
          1, 2, 3, 1.5, 90, 1, NULL, 'test', 'test', ?)
        """,
        (event_id, conf, now),
    )
    conn.execute(
        """
        INSERT INTO market_events_market_intelligence_f7 (
          event_id, market_score, component_scores_json, final_confidence,
          success_probability, liquidation_regime, liquidation_intel_json,
          dominance_regime, dominance_json, whale_score, whale_intel_json,
          news_impact, news_keywords_json, long_trend_stage, long_trend_json,
          image_intel_json, interest_factors_json, telegram_rendered, created_at
        ) VALUES (?, ?, '{}', ?, 0.75, 'neutral', '{}', 'RISK ON', '{}',
          0, '{}', 'LOW', '[]', NULL, '{}', '{}', '[]', 'test', ?)
        """,
        (event_id, mscore, conf, now),
    )


class ResearchG2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {
            "ME_G2_CLAUDE_RESEARCH": "true",
            "ME_G2_MIN_CONFIDENCE": "7.0",
            "ME_G2_MIN_MARKET_SCORE": "60",
        }, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_schema_v26(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v26", applied)
            self.assertEqual(SCHEMA_VERSION, 26)

    def test_eligibility_requires_f7_confidence(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            _seed_g1_row(conn, eid)
            _seed_f5_f7(conn, eid, conf=6.5, mscore=70)
            ok, reason = check_g2_eligibility(conn, eid)
            self.assertFalse(ok)
            self.assertEqual(reason, "low_confidence")

    def test_eligibility_passes(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            _seed_g1_row(conn, eid)
            _seed_f5_f7(conn, eid, conf=7.5, mscore=65)
            ok, _ = check_g2_eligibility(conn, eid)
            self.assertTrue(ok)

    def test_research_persisted_deterministic(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC", ret=-3.0)
            seed_candles(conn, symbol="BTC", n=40, shock=True)
            _seed_g1_row(conn, eid)
            _seed_f5_f7(conn, eid)
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
                ) VALUES (?, 'aligned', '[]', 'flattening', 'rising', 2.0, 1.5, 0.2, 'high',
                  '{}', '[]', 70, 1.5, 0, '{}', 'against', 10, 7, 0.78, '[]',
                  7.5, '{}', 0.75, 'wait', 2.0, 1.0, 'test', 'test', 'f2', ?)
                """,
                (eid, int(time.time())),
            )
            result = run_claude_research_g2(conn, eid)
            self.assertIsNotNone(result)
            row = conn.execute(
                "SELECT market_story, bullish_factors_json, summary_ru, confidence, market_score "
                "FROM market_events_ai_research_g2 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(row["market_story"])
            self.assertGreaterEqual(float(row["confidence"]), 7.0)
            self.assertGreaterEqual(float(row["market_score"]), 60)

    def test_telegram_block_compact(self) -> None:
        text = format_research_telegram_g2(
            reversal_factors=["капитуляция", "отрицательный Funding"],
            risks=["BTC остаётся слабым"],
            summary_ru="Ждать подтверждения R2.",
        )
        self.assertIn("🧠 Research Agent", text)
        self.assertIn("За откат:", text)
        self.assertIn("Риски:", text)
        self.assertIn("Итог:", text)
        self.assertLessEqual(len(text.splitlines()), 10)

    def test_structured_json_schema(self) -> None:
        ctx = {
            "g1": {"capitulation": {"volume_multiple": 3.8}, "liquidity_accum": {"funding_negative": True, "oi_rising": True},
                   "reversal_probability": 0.8, "continuation_probability": 0.2},
            "market_event": {"return_pct": -2.5},
            "f2": {"correlation_verdict": "against"},
            "f7": {"final_confidence": 7.5, "market_score": 65},
            "trend_v2": {"stage": "Capitulation"},
        }
        out = analyze_research_g2_deterministic(ctx)
        self.assertIn("market_story", out)
        self.assertIn("bullish_factors", out)
        self.assertIn("reversal_probability", out)
        self.assertIn("summary_ru", out)

    def test_learning_note_saved(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC")
            seed_candles(conn, symbol="BTC", n=30)
            record_paper_learning_g2(
                conn, event_id=eid, symbol="BTC",
                entry_ts=int(time.time()) - 3600,
                entry_price=100.0, net_return=1.2,
                exit_reason="TP", duration_seconds=900,
            )
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_g2_learning_notes WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertGreaterEqual(int(row["n"]), 1)

    def test_cost_estimate(self) -> None:
        cost = _estimate_cost("claude-sonnet-5", 1000, 500)
        self.assertGreater(cost, 0)


class ClaudeClientG2Tests(unittest.TestCase):
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False)
    def test_not_configured_without_key(self) -> None:
        self.assertFalse(is_claude_configured())

    @patch("bot.research.market_events.signal_intelligence.claude_client_g2.requests.post")
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "ME_G2_CLAUDE_MODEL": "claude-sonnet-5"}, clear=False)
    def test_call_claude_json(self, mock_post: MagicMock) -> None:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import call_claude_json_g2

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "content": [{"type": "text", "text": json.dumps({
                    "market_story": "test",
                    "bullish_factors": ["a"],
                    "bearish_factors": [],
                    "reversal_probability": 0.7,
                    "continuation_probability": 0.3,
                    "risks": ["b"],
                    "invalidates": ["c"],
                    "summary_ru": "Ждать R2.",
                })}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        )
        mock_post.return_value.raise_for_status = MagicMock()
        parsed, resp = call_claude_json_g2(system="test", prompt="{}", label="test")
        self.assertEqual(parsed["summary_ru"], "Ждать R2.")
        self.assertEqual(resp.usage.input_tokens, 100)
