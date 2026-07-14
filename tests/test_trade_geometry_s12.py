"""Phase S1.2 — Trade geometry validator tests."""

from __future__ import annotations

import unittest

from bot.research.market_events.signal_intelligence.trade_geometry_s12 import (
    shock_direction_for_trade_side,
    validate_trade_geometry_s12,
)
from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5, compute_risk_reward_f5
from bot.research.market_events.signal_intelligence.trade_plan_f71 import compute_trade_plan_f71


class TradeGeometryS12Tests(unittest.TestCase):
    def test_long_valid(self) -> None:
        c = validate_trade_geometry_s12(
            direction="LONG", entry=100, tp1=101, tp2=102, sl=99,
        )
        self.assertTrue(c.ok)

    def test_long_tp_below_entry(self) -> None:
        c = validate_trade_geometry_s12(
            direction="LONG", entry=64693, tp1=63969, tp2=63000, sl=65000,
        )
        self.assertFalse(c.ok)
        self.assertIn("TP below Entry", c.errors)
        log = c.format_invalid_log()
        self.assertIn("SIGNAL INVALID", log)
        self.assertIn("Direction", log)
        self.assertIn("LONG", log)
        self.assertIn("TP below Entry", log)

    def test_short_valid(self) -> None:
        c = validate_trade_geometry_s12(
            direction="SHORT", entry=100, tp1=99, tp2=98, sl=101,
        )
        self.assertTrue(c.ok)

    def test_short_tp_above_entry(self) -> None:
        c = validate_trade_geometry_s12(
            direction="SHORT", entry=100, tp1=101, tp2=102, sl=99,
        )
        self.assertFalse(c.ok)
        self.assertIn("TP above Entry", c.errors)

    def test_shock_mapping_produces_valid_geometry(self) -> None:
        rr = compute_risk_reward_f5(
            expected_target_pct=2.5,
            expected_stop_pct=1.0,
            reversal_probability=0.7,
            dynamic_confidence=6.0,
            historical_reversal_rate=0.7,
        )
        for side in ("LONG", "SHORT"):
            plan = compute_trade_plan_f71(
                price=64693.0,
                shock_direction=shock_direction_for_trade_side(side),
                risk_reward=rr,
                final_confidence=6.0,
            )
            self.assertEqual(plan.trade_side, side)
            check = validate_trade_geometry_s12(
                direction=side,
                entry=plan.entry,
                tp1=plan.tp1,
                tp2=plan.tp2,
                sl=plan.sl,
                tp3=plan.tp3,
            )
            self.assertTrue(check.ok, msg=f"{side}: {check.errors} plan={plan}")

    def test_old_inverted_mapping_fails_geometry(self) -> None:
        """Historical bug: LONG used shock UP → SHORT prices labeled LONG."""
        rr = compute_risk_reward_f5(
            expected_target_pct=2.5,
            expected_stop_pct=1.0,
            reversal_probability=0.7,
            dynamic_confidence=6.0,
            historical_reversal_rate=0.7,
        )
        plan = compute_trade_plan_f71(
            price=64693.0,
            shock_direction="UP",  # inverted
            risk_reward=rr,
            final_confidence=6.0,
        )
        check = validate_trade_geometry_s12(
            direction="LONG",  # label LONG with SHORT prices
            entry=plan.entry,
            tp1=plan.tp1,
            tp2=plan.tp2,
            sl=plan.sl,
        )
        self.assertFalse(check.ok)
        self.assertIn("TP below Entry", check.errors)


if __name__ == "__main__":
    unittest.main()
