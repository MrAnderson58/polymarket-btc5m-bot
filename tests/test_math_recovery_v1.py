"""Signal Mathematics Recovery V1 — unit tests (read-only helpers)."""

from __future__ import annotations

import unittest

from bot.research.market_events.signal_intelligence.math_recovery_v1.feature_audit import (
    audit_feature_values,
    classify_status,
)
from bot.research.market_events.signal_intelligence.math_recovery_v1.pipeline_flow import (
    CRITICAL_DROP,
    _drop,
)


class TestMathRecoveryV1(unittest.TestCase):
    def test_classify_empty_constant_good(self) -> None:
        now = 1_700_000_000
        self.assertEqual(
            classify_status(fill_pct=0.0, n_unique=0, first_seen=None, last_seen=None, now=now),
            "EMPTY",
        )
        self.assertEqual(
            classify_status(
                fill_pct=100.0, n_unique=1, first_seen=now, last_seen=now, now=now,
            ),
            "CONSTANT",
        )
        self.assertEqual(
            classify_status(
                fill_pct=99.0, n_unique=20, first_seen=now, last_seen=now, now=now,
            ),
            "GOOD",
        )
        self.assertEqual(
            classify_status(
                fill_pct=0.0,
                n_unique=0,
                first_seen=None,
                last_seen=None,
                now=now,
                broken_source=True,
            ),
            "BROKEN",
        )

    def test_audit_feature_constant(self) -> None:
        now = 1_700_000_000
        pairs = [(50.0, -1.0, now)] * 20
        row = audit_feature_values("atr", pairs, now=now)
        self.assertEqual(row["status"], "CONSTANT")
        self.assertEqual(row["unique_values"], 1)
        self.assertEqual(row["percent_filled"], 100.0)

    def test_drop_critical(self) -> None:
        self.assertGreaterEqual(_drop(100, 5) / 100.0, CRITICAL_DROP)
        self.assertLess(_drop(100, 50) / 100.0, CRITICAL_DROP)


if __name__ == "__main__":
    unittest.main()
