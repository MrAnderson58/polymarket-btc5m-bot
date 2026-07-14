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
from bot.research.market_events.signal_intelligence.claude_ops_g2 import (
    AI_STATUS_SKIPPED,
    format_claude_health_report,
    get_daily_usage,
    try_consume_claude_quota,
)
from bot.research.market_events.signal_intelligence.learning_g2 import record_paper_learning_g2
from bot.research.market_events.signal_intelligence.research_g2 import (
    analyze_research_g2_deterministic,
    build_g2_compact_summary,
    build_g2_context,
    check_g2_eligibility,
    estimate_g2_prompt_tokens,
    format_g2_trace,
    resolve_latest_g2_event_id,
    run_claude_research_g2,
    run_research_g2,
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

    def test_schema_v28(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v28", applied)
            self.assertEqual(SCHEMA_VERSION, 38)

    def test_schema_v27(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v27", applied)
            self.assertEqual(SCHEMA_VERSION, 38)

    def test_schema_v26(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v26", applied)
            self.assertEqual(SCHEMA_VERSION, 38)

    def test_eligibility_requires_f7_confidence(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            _seed_g1_row(conn, eid)
            _seed_f5_f7(conn, eid, conf=6.5, mscore=70)
            ok, reason = check_g2_eligibility(conn, eid)
            self.assertFalse(ok)
            self.assertEqual(reason, "confidence 6.5")

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

    def _seed_full_event(self, conn, eid: int) -> None:
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

    def test_event_cache_skips_second_analysis(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC", ret=-3.0)
            self._seed_full_event(conn, eid)
            first = run_claude_research_g2(conn, eid)
            second = run_claude_research_g2(conn, eid)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_ai_research_g2 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertEqual(int(count["n"]), 1)

    @patch("bot.research.market_events.signal_intelligence.claude_client_g2.call_claude_json_g2")
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False)
    def test_ai_skipped_on_invalid_json(self, mock_call: MagicMock) -> None:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import ClaudeClientError

        mock_call.side_effect = ClaudeClientError("invalid_json", "bad json")
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC", ret=-3.0)
            self._seed_full_event(conn, eid)
            result = run_claude_research_g2(conn, eid)
            self.assertIsNotNone(result)
            row = conn.execute(
                "SELECT ai_status, skip_error, provider FROM market_events_ai_research_g2 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertEqual(row["ai_status"], AI_STATUS_SKIPPED)
            self.assertIn("bad json", row["skip_error"])
            self.assertEqual(row["provider"], "deterministic")

    def test_daily_rate_limit(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            with patch(
                "bot.research.market_events.signal_intelligence.claude_ops_g2.G2_DAILY_REQUEST_LIMIT",
                2,
            ):
                ok1, _ = try_consume_claude_quota(conn)
                ok2, _ = try_consume_claude_quota(conn)
                ok3, reason = try_consume_claude_quota(conn)
                self.assertTrue(ok1 and ok2)
                self.assertFalse(ok3)
                self.assertIn("daily_limit", reason or "")
                usage = get_daily_usage(conn)
                self.assertEqual(usage["requests_today"], 2)

    def test_claude_health_report(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            text = format_claude_health_report(conn)
            self.assertIn("CLAUDE HEALTH", text)
            self.assertIn("Requests:", text)

    def test_run_research_g2_records_trace(self) -> None:
        from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
            STAGE_G2_COMPLETED,
            STAGE_G2_STARTED,
        )

        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC", ret=-3.0)
            self._seed_full_event(conn, eid)
            result = run_research_g2(conn, eid)
            self.assertIsNotNone(result)
            rows = conn.execute(
                "SELECT stage FROM market_events_signal_trace_f51 WHERE event_id = ?",
                (eid,),
            ).fetchall()
            stages = {r["stage"] for r in rows}
            self.assertIn(STAGE_G2_STARTED, stages)
            self.assertIn(STAGE_G2_COMPLETED, stages)

    def test_g2_trace_format(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC", ret=-3.0)
            self._seed_full_event(conn, eid)
            run_research_g2(conn, eid)
            text = format_g2_trace(conn, eid)
            self.assertIn(f"Event {eid}", text)
            self.assertIn("F5", text)
            self.assertIn("F7", text)
            self.assertIn("G2", text)

    def test_compact_prompt_under_budget(self) -> None:
        ctx = {
            "symbol": "BTC",
            "g1": {
                "signal_type": "CAPITULATION",
                "reversal_probability": 0.8,
                "continuation_probability": 0.2,
                "liquidity_accum": {"funding_negative": True, "oi_rising": True},
                "capitulation": {"volume_multiple": 3.5},
                "consecutive_patterns": [{"max_streak": 9, "description": "9 red"}],
            },
            "market_event": {"return_pct": -3.2, "direction": "DOWN"},
            "f5": {"dynamic_confidence": 7.5, "reversal_probability": 0.7, "signal_cause": "shock"},
            "f7": {"market_score": 65, "final_confidence": 7.5, "liquidation_regime": "neutral", "dominance_regime": "RISK ON"},
            "f2": {"correlation_verdict": "against", "historical_reversal_rate": 0.78},
            "trend_v2": {"stage": "Capitulation"},
        }
        est = estimate_g2_prompt_tokens(ctx)
        self.assertLess(est, 1500)

    def test_prompt_cache_reuses_response(self) -> None:
        from bot.research.market_events.signal_intelligence.claude_cache_g2 import (
            compute_g2_context_hash,
            load_prompt_cache,
            save_prompt_cache,
        )

        ctx = {
            "symbol": "BTC",
            "g1": {"signal_type": "CAP", "reversal_probability": 0.7, "continuation_probability": 0.3,
                   "liquidity_accum": {}, "consecutive_patterns": []},
            "market_event": {"return_pct": -2.0, "direction": "DOWN"},
            "f5": {"dynamic_confidence": 7.0, "reversal_probability": 0.6, "signal_cause": "x"},
            "f7": {"market_score": 60, "final_confidence": 7.0},
            "f2": {"historical_reversal_rate": 0.5},
            "trend_v2": None,
        }
        compact = build_g2_compact_summary(ctx)
        h = compute_g2_context_hash(compact)
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            save_prompt_cache(
                conn, context_hash=h,
                response={"market_story": "t", "bullish_factors": [], "bearish_factors": [],
                          "reversal_probability": 0.6, "continuation_probability": 0.4,
                          "risks": [], "invalidates": [], "summary_ru": "ok"},
                provider="anthropic", model="test", input_tokens=100, output_tokens=50, cost_usd=0.001,
            )
            cached = load_prompt_cache(conn, h)
            self.assertIsNotNone(cached)
            self.assertEqual(cached["parsed"]["summary_ru"], "ok")

    def test_g2_trace_latest_event(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC", ret=-3.0)
            self._seed_full_event(conn, eid)
            run_research_g2(conn, eid)
            latest = resolve_latest_g2_event_id(conn)
            self.assertEqual(latest, eid)
            text = format_g2_trace(conn, None)
            self.assertIn(f"Event {eid}", text)
            self.assertNotIn("missing", text.lower())


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
