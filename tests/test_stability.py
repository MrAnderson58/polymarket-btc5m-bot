"""Tests for parameter stability with optimizer walk-forward format."""

from __future__ import annotations

import unittest

from bot.analytics.stability import build_parameter_stability


class ParameterStabilityTestCase(unittest.TestCase):
    def test_optimizer_walk_forward_format_no_crash(self) -> None:
        report = {
            "parameter_optimizer": {
                "current": {"entry": 0.40, "stop_pct": -10, "trailing_activation": 0.03, "trades": 50},
                "optimal": {"entry": 0.39, "stop_pct": -15, "trailing_activation": 0.03},
                "entry_significance": [],
            },
            "walk_forward": {
                "rows": [
                    {
                        "train_size": 300,
                        "test_size": 100,
                        "train_avg_pnl": 2.1,
                        "test_avg_pnl": 1.5,
                        "train_pf": 1.4,
                        "test_pf": 1.2,
                        "generalizes": True,
                    }
                ],
                "trend": "stable",
            },
            "monte_carlo": {"probability_of_ruin": 0.05},
            "equity_curve": {"rolling_pf": [1.1, 1.2, 1.3]},
        }
        result = build_parameter_stability(report)
        self.assertIn("parameters", result)
        self.assertTrue(result["parameters"])

    def test_missing_win_rate_rows_skipped(self) -> None:
        report = {
            "parameter_optimizer": {
                "current": {"stop_pct": -10, "trailing_activation": 0.03, "trades": 20},
                "optimal": {},
                "entry_significance": [
                    {"entry_price": 0.38, "trades": 10, "profit_factor": 1.2, "avg_pnl": 1.0}
                ],
            },
            "walk_forward": {"rows": [], "trend": "insufficient_data"},
            "monte_carlo": {},
            "equity_curve": {},
        }
        result = build_parameter_stability(report)
        self.assertEqual(len(result["parameters"]), 2)
        self.assertTrue(all(p["parameter"] != "entry" for p in result["parameters"]))


if __name__ == "__main__":
    unittest.main()
