"""Tests for Confidence Engine."""

from __future__ import annotations

import unittest

from bot.ai_agent.confidence import compute_confidence


class ConfidenceTestCase(unittest.TestCase):
    def test_high_sample_high_pf(self) -> None:
        conf = compute_confidence(
            ai_score=83,
            similar={
                "similar_count": 274,
                "profit_factor": 2.81,
                "win_rate": 0.59,
                "avg_distance": 1.0,
            },
        )
        self.assertGreaterEqual(conf, 70)

    def test_low_sample_low_confidence(self) -> None:
        conf = compute_confidence(
            ai_score=55,
            similar={"similar_count": 3, "profit_factor": 0.8, "win_rate": 0.3},
        )
        self.assertLess(conf, 60)
