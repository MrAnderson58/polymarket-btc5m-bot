"""Phase F.7.1 — operations CLI and trade plan tests."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.live_dashboard_f71 import render_live_dashboard
from bot.research.market_events.signal_intelligence.ops_reports_f71 import (
    today_summary_report,
)
from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5
from bot.research.market_events.signal_intelligence.telegram_f7 import render_professional_telegram_f7
from bot.research.market_events.signal_intelligence.trade_plan_f71 import (
    compute_trade_plan_f71,
    compute_position_size_pct,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_event


class OpsF71Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {}, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_trade_plan_long(self) -> None:
        rr = RiskRewardF5(
            risk_reward=3.7, tp1_prob=70, tp2_prob=45, tp3_prob=25,
            tp1_pct=0.9, tp2_pct=2.4, tp3_pct=5.4, stop_pct=1.3,
        )
        plan = compute_trade_plan_f71(
            price=141.80,
            shock_direction="DOWN",
            risk_reward=rr,
            final_confidence=9.3,
        )
        self.assertEqual(plan.trade_side, "LONG")
        self.assertLess(plan.sl, plan.entry)
        self.assertGreater(plan.tp1, plan.entry)
        self.assertAlmostEqual(plan.risk_reward, 3.7, places=1)
        self.assertGreaterEqual(plan.position_size_pct, 4.0)

    def test_position_size_scales_with_confidence(self) -> None:
        low = compute_position_size_pct(final_confidence=7.0, risk_reward=2.0)
        high = compute_position_size_pct(final_confidence=9.3, risk_reward=3.7)
        self.assertLess(low, high)

    def test_today_summary(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            now = int(time.time())
            seed_event(conn, symbol="SOL", ret=-4.0)
            conn.execute(
                "UPDATE market_events SET event_ts = ? WHERE symbol = 'SOL'",
                (now,),
            )
            conn.commit()
            text = today_summary_report(conn)
        self.assertIn("TODAY", text)
        self.assertIn("Signals:", text)
        self.assertIn("SOL", text)

    def test_telegram_trade_plan_format(self) -> None:
        rr = RiskRewardF5(
            risk_reward=3.7, tp1_prob=70, tp2_prob=45, tp3_prob=25,
            tp1_pct=0.9, tp2_pct=2.4, tp3_pct=5.4, stop_pct=1.3,
        )
        plan = compute_trade_plan_f71(
            price=141.80, shock_direction="DOWN", risk_reward=rr, final_confidence=9.3,
        )
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="SOL", ret=-5.0)
            text = render_professional_telegram_f7(
                conn,
                event_id=eid,
                symbol="SOL",
                direction="DOWN",
                final_confidence=9.3,
                success_probability=0.82,
                market_score=79,
                risk_reward=rr,
                ai_summary="Откат вероятен.",
                report=None,
                trend=None,
                liq_intel={"regime": "long_squeeze"},
                dominance={"btc_return": 0.1},
                long_trend=None,
                image_intel=None,
                historical_rate=0.78,
                trade_plan=plan,
            )
        self.assertIn("SOLUSDT", text)
        self.assertIn("141.80", text)
        self.assertIn("Размер позиции", text)
        self.assertRegex(text, r"\d+%")
        self.assertIn("✔ ликвидации", text)
        self.assertIn("PAPER ONLY", text)

    def test_live_dashboard_render(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            text = render_live_dashboard(conn)
        self.assertIn("LIVE DASHBOARD", text)
        self.assertIn("BTC", text)
        self.assertIn("Signals today", text)
