"""Phase F.7 — Market Intelligence Engine tests."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.liquidation_intelligence_f7 import analyze_liquidations
from bot.research.market_events.signal_intelligence.market_score_f7 import (
    apply_market_score_multiplier,
    compute_market_score,
)
from bot.research.market_events.signal_intelligence.news_weight_f7 import (
    IMPACT_HIGH,
    analyze_news_weight,
)
from bot.research.market_events.signal_intelligence.telegram_f7 import render_professional_telegram_f7
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


class MarketIntelligenceF7Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {
            "ME_F2_PROFESSIONAL_INTEL": "true",
            "ME_F5_PROFESSIONAL_SIGNAL": "true",
            "ME_F7_MARKET_INTELLIGENCE": "true",
            "ME_F7_MIN_MARKET_SCORE": "40",
            "ME_F7_MIN_FINAL_CONFIDENCE": "5.0",
        }, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_schema_v21(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v22", applied)
            self.assertEqual(SCHEMA_VERSION, 33)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_events_market_intelligence_f7", tables)

    def test_market_score_multiplier(self) -> None:
        self.assertAlmostEqual(apply_market_score_multiplier(9.0, 100), 9.0, places=1)
        self.assertLess(apply_market_score_multiplier(9.0, 50), 9.0)
        self.assertGreater(apply_market_score_multiplier(9.0, 50), 4.0)

    def test_market_score_weights(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="BTC")
            seed_candles(conn, symbol="ETH")
            score = compute_market_score(
                conn, report=None, trend=None,
                dominance_regime="RISK ON", liq_intel={"regime": "neutral"},
            )
            self.assertGreaterEqual(score.score, 0)
            self.assertLessEqual(score.score, 100)
            self.assertEqual(len(score.components), 9)

    def test_liquidation_intel(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SUI", shock=True)
            liq = analyze_liquidations(
                conn, symbol="SUI", trend={"stage": "Capitulation"}, shock_return=-6.0,
            )
            self.assertGreater(liq.reversal_probability, 0)
            self.assertIn("bybit", liq.exchanges)

    def test_news_weight_iran(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC", ret=-4.0)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_event_context (
                  event_id, context_type, source, source_record_id, context_ts,
                  time_delta_seconds, relevance_score, context_json, created_at
                ) VALUES (?, 'NEWS', 'reuters', 'news-1', ?, 0, 0.9, ?, ?)
                """,
                (
                    eid, now,
                    '{"headline": "Israel strikes Iran nuclear site", "raw_text": "Iran retaliation expected"}',
                    now,
                ),
            )
            news = analyze_news_weight(conn, event_id=eid)
            self.assertEqual(news.impact, IMPACT_HIGH)
            self.assertTrue(any("iran" in k.lower() for k in news.keywords))

    def test_telegram_v3_format(self) -> None:
        from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5

        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="MANTA", ret=-5.2)
            seed_candles(conn, symbol="MANTA", n=10, shock=False)
            rr = RiskRewardF5(
                risk_reward=2.8, tp1_prob=70, tp2_prob=45, tp3_prob=25,
                tp1_pct=2.0, tp2_pct=4.0, tp3_pct=6.0, stop_pct=2.5,
            )
            text = render_professional_telegram_f7(
                conn,
                event_id=eid,
                symbol="MANTA",
                direction="DOWN",
                final_confidence=9.2,
                success_probability=0.82,
                market_score=79,
                risk_reward=rr,
                ai_summary="Откат вероятен после капитуляции.",
                report=None,
                trend=None,
                liq_intel={"exhaustion": True, "regime": "long_squeeze"},
                dominance={"regime": "RISK ON", "btc_return": 0.2},
                long_trend=None,
                image_intel=None,
                historical_rate=0.76,
            )
            self.assertIn("MANTAUSDT", text)
            self.assertIn("9.2", text)
            self.assertIn("Размер позиции", text)
            self.assertNotIn("ENTRY_NEAR_MARKET", text)
            self.assertIn("PAPER ONLY", text)
