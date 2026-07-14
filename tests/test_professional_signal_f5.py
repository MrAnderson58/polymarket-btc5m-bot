"""Phase F.5 — Professional Signal Engine tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.dynamic_confidence_f5 import compute_dynamic_confidence
from bot.research.market_events.signal_intelligence.entry_quality_f5 import (
    GRADE_A_PLUS,
    GRADE_SKIP,
    grade_entry_quality,
)
from bot.research.market_events.signal_intelligence.priority_engine_f5 import (
    PRIORITY_WINDOW_SEC,
    should_send_f5_telegram,
)
from bot.research.market_events.signal_intelligence.professional_signal_f5 import run_signal_engine_f5
from bot.research.market_events.signal_intelligence.risk_reward_f5 import compute_risk_reward_f5
from bot.research.market_events.signal_intelligence.signal_report_f2 import run_signal_report_f2
from bot.research.market_events.signal_intelligence.telegram_f5 import render_professional_telegram_f5
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


class ProfessionalSignalF5Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {
            "ME_F2_PROFESSIONAL_INTEL": "true",
            "ME_F5_PROFESSIONAL_SIGNAL": "true",
            "ME_F5_MIN_TELEGRAM_CONFIDENCE": "7.0",
            "ME_F5_TOP_N_TELEGRAM": "3",
        }, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_schema_v18(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v18", applied)
            self.assertEqual(SCHEMA_VERSION, 42)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_events_signal_reports_f5", tables)
            self.assertIn("market_events_signal_priority_f5", tables)

    def test_dynamic_confidence_synergy(self) -> None:
        score, bonuses, active = compute_dynamic_confidence(
            6.5,
            consecutive_bars=17,
            volume_multiple=5.3,
            funding_negative=True,
            oi_stalled=True,
            demand_zone=True,
            historical_reversal_rate=0.79,
            trend_stage="Capitulation",
            liquidation_signal=True,
            support_lost=True,
        )
        self.assertGreaterEqual(score, 9.0)
        self.assertIn("synergy", bonuses)
        self.assertGreaterEqual(len(active), 4)

    def test_entry_quality_grades(self) -> None:
        self.assertEqual(
            grade_entry_quality(
                dynamic_confidence=9.4,
                reversal_probability=0.81,
                entry_recommendation="WAIT_R2",
                historical_reversal_rate=0.79,
            ),
            GRADE_A_PLUS,
        )
        self.assertEqual(
            grade_entry_quality(
                dynamic_confidence=4.0,
                reversal_probability=0.3,
                entry_recommendation="SKIP",
                historical_reversal_rate=0.2,
            ),
            GRADE_SKIP,
        )

    def test_risk_reward_block(self) -> None:
        rr = compute_risk_reward_f5(
            expected_target_pct=4.5,
            expected_stop_pct=1.2,
            reversal_probability=0.81,
            dynamic_confidence=9.4,
            historical_reversal_rate=0.79,
        )
        self.assertGreater(rr.risk_reward, 2.0)
        self.assertGreaterEqual(rr.tp1_prob, 70)

    def test_telegram_layout_contains_opportunity_framing(self) -> None:
        rr = compute_risk_reward_f5(
            expected_target_pct=4.0,
            expected_stop_pct=1.1,
            reversal_probability=0.81,
            dynamic_confidence=9.4,
            historical_reversal_rate=0.79,
        )
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="SOL", ret=-7.2)
            msg = render_professional_telegram_f5(
                conn,
                event_id=eid,
                dynamic_confidence=9.4,
                reversal_probability=0.81,
                continuation_probability=0.19,
                entry_quality="A+",
                signal_cause="ликвидации + Funding + OI",
                interest_factors=[
                    "✔ 17 красных свечей подряд",
                    "✔ объём 5.3× среднего",
                ],
                historical_examples=[{
                    "date_label": "12 марта",
                    "symbol": "BTC",
                    "shock_pct": -8.0,
                    "reversal_pct": 5.1,
                    "hours": 6,
                }],
                risk_reward=rr,
                ai_summary="Похоже на снятие ликвидности.\nВероятность паники высокая.",
                trend={"window_minutes": 45, "cumulative_return_pct": -7.2, "consecutive_bars": 17},
                report=type("R", (), {"entry_recommendation": "WAIT_R2"})(),
            )
        self.assertIn("🚨 SOLUSDT", msg)
        self.assertIn("TREND SHOCK", msg)
        self.assertIn("9.4 / 10", msg)
        self.assertIn("81%", msg)
        self.assertIn("Вероятность отката", msg)
        self.assertIn("Почему бот считает это интересным", msg)
        self.assertIn("Ждать реакцию R2", msg)
        self.assertIn("RR =", msg)
        self.assertIn("PAPER ONLY", msg)
        self.assertIn("ликвидации", msg)

    def test_priority_top3_only(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            bucket = 999_999
            for i, conf in enumerate([9.5, 9.0, 8.5], start=1):
                eid = seed_event(conn, symbol=f"SYM{i}", ret=-3.0)
                conn.execute(
                    """
                    INSERT INTO market_events_signal_priority_f5 (
                      event_id, dynamic_confidence, priority_score, rank_position,
                      window_bucket, telegram_sent, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 1)
                    """,
                    (eid, conf, conf * 10, i, bucket),
                )
            new_eid = seed_event(conn, symbol="NEW", ret=-4.0)
            with patch(
                "bot.research.market_events.signal_intelligence.priority_engine_f5._window_bucket",
                return_value=bucket,
            ):
                ok, reason = should_send_f5_telegram(
                    conn, event_id=new_eid, dynamic_confidence=9.8, priority_score=120, min_confidence=7.0,
                )
            self.assertFalse(ok)
            self.assertIn(reason, ("window_full", "not_top3"))

    def test_low_confidence_dashboard_only(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            with patch(
                "bot.research.market_events.signal_intelligence.priority_engine_f5._window_bucket",
                return_value=12345,
            ):
                ok, reason = should_send_f5_telegram(
                    conn, event_id=1, dynamic_confidence=6.2, priority_score=50, min_confidence=7.0,
                )
            self.assertFalse(ok)
            self.assertEqual(reason, "low_confidence")

    def test_run_signal_engine_persists(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SOL", n=120)
            eid = seed_event(conn, symbol="SOL", ret=-7.2)
            run_signal_report_f2(conn, eid)
            signal = run_signal_engine_f5(conn, eid)
            self.assertIsNotNone(signal)
            row = conn.execute(
                "SELECT dynamic_confidence, entry_quality FROM market_events_signal_reports_f5 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertGreater(float(row["dynamic_confidence"]), 0)


if __name__ == "__main__":
    unittest.main()
