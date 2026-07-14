"""Phase F.7.2 — signal outcome engine tests."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5
from bot.research.market_events.signal_intelligence.signal_outcome_f72 import (
    STATUS_ACTIVE,
    STATUS_CLOSED,
    STATUS_TP1,
    create_active_signal_f72,
    tick_active_signals_f72,
)
from bot.research.market_events.signal_intelligence.trade_plan_f71 import compute_trade_plan_f71
from bot.research.market_events.signal_intelligence.yesterday_report_f72 import (
    outcome_dashboard_stats,
    yesterday_report,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


def _seed_f5_f7(conn, event_id: int) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_events_signal_reports_f5 (
          event_id, dynamic_confidence, reversal_probability, continuation_probability,
          entry_quality, signal_cause, interest_factors_json, historical_examples_json,
          risk_reward, tp_probabilities_json, tp1_pct, tp2_pct, tp3_pct, stop_pct,
          priority_score, telegram_eligible, telegram_skip_reason, ai_summary_ru,
          telegram_rendered, created_at
        ) VALUES (?, 9.0, 0.8, 0.2, 'A', 'test', '[]', '[]', 3.0, '{}',
          1.0, 2.0, 3.0, 1.5, 90, 1, NULL, 'test', 'test', ?)
        """,
        (event_id, now),
    )
    conn.execute(
        """
        INSERT INTO market_events_market_intelligence_f7 (
          event_id, market_score, component_scores_json, final_confidence,
          success_probability, liquidation_regime, liquidation_intel_json,
          dominance_regime, dominance_json, whale_score, whale_intel_json,
          news_impact, news_keywords_json, long_trend_stage, long_trend_json,
          image_intel_json, interest_factors_json, telegram_rendered, created_at
        ) VALUES (?, 75, '{}', 9.0, 0.8, 'neutral', '{}', 'RISK ON', '{}',
          0, '{}', 'LOW', '[]', NULL, '{}', '{}', '[]', 'test', ?)
        """,
        (event_id, now),
    )


class SignalOutcomeF72Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {
            "ME_F72_SIGNAL_OUTCOME": "true",
            "ME_F5_PROFESSIONAL_SIGNAL": "true",
        }, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_schema_v22(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v22", applied)
            self.assertIn("v23", applied)
            self.assertEqual(SCHEMA_VERSION, 39)

    def test_create_and_tp1_hit(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="SOL", ret=-5.0)
            seed_candles(conn, symbol="SOL", n=10, shock=False)
            _seed_f5_f7(conn, eid)
            conn.commit()

            outcome = create_active_signal_f72(conn, event_id=eid)
            self.assertIsNotNone(outcome)
            self.assertEqual(outcome.status, STATUS_ACTIVE)

            row = conn.execute(
                "SELECT tp1 FROM market_events_signal_outcomes_f72 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            tp1 = float(row["tp1"])

            tick_active_signals_f72(conn, feed=None, prices={"SOL": tp1 + 0.01})
            status = conn.execute(
                "SELECT status FROM market_events_signal_outcomes_f72 WHERE event_id = ?",
                (eid,),
            ).fetchone()["status"]
            self.assertEqual(status, STATUS_TP1)

    def test_sl_closes_signal(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="ETH", ret=-4.0)
            seed_candles(conn, symbol="ETH", n=10, shock=False)
            _seed_f5_f7(conn, eid)
            conn.commit()
            create_active_signal_f72(conn, event_id=eid)
            row = conn.execute(
                "SELECT sl FROM market_events_signal_outcomes_f72 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            sl = float(row["sl"])
            tick_active_signals_f72(conn, feed=None, prices={"ETH": sl - 0.01})
            status = conn.execute(
                "SELECT status, exit_reason FROM market_events_signal_outcomes_f72 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertEqual(status["status"], STATUS_CLOSED)
            self.assertEqual(status["exit_reason"], "SL")

    def test_yesterday_report_empty(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            text = yesterday_report(conn)
        self.assertIn("Вчера", text)
        self.assertIn("Win Rate", text)

    def test_dashboard_stats(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            stats = outcome_dashboard_stats(conn)
        self.assertIn("active_signals", stats)
        self.assertIn("win_rate", stats)

    def test_trade_plan_long_levels(self) -> None:
        rr = RiskRewardF5(
            risk_reward=3.7, tp1_prob=70, tp2_prob=45, tp3_prob=25,
            tp1_pct=1.0, tp2_pct=2.0, tp3_pct=3.0, stop_pct=1.5,
        )
        plan = compute_trade_plan_f71(
            price=100.0, shock_direction="DOWN", risk_reward=rr, final_confidence=9.0,
        )
        self.assertGreater(plan.tp1, plan.entry)
        self.assertLess(plan.sl, plan.entry)
