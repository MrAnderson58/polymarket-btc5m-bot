"""Tests for Research Consistency Audit V1."""

from __future__ import annotations

import unittest

from bot.research.market_events.signal_intelligence.research_probe_v1 import (
    probe_matches,
    probe_time_label,
)
from bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine import (
    format_audit_terminal,
    run_research_consistency_audit_v1,
)


class TestProbe(unittest.TestCase):
    def test_probe_matches(self):
        a = {"ok": True, "symbol": "BTC", "trade_id": 100}
        b = {"ok": True, "symbol": "BTC", "trade_id": 100}
        self.assertTrue(probe_matches(a, b))
        self.assertFalse(probe_matches(a, {"ok": True, "symbol": "ETH", "trade_id": 100}))

    def test_time_label_now(self):
        import time
        now = int(time.time())
        self.assertEqual(probe_time_label(now - 3600, now=now), "now")


class TestAuditFormat(unittest.TestCase):
    def test_terminal_has_table(self):
        text = format_audit_terminal({
            "ok": True,
            "n_lake": 100,
            "db_name": "market_events.db",
            "db_source": "env",
            "canonical_probe": {"symbol": "BTC", "trade_id": 1, "time_label": "now"},
            "rows": [
                {
                    "engine": "Fingerprint",
                    "source": "research_lake_v1",
                    "coin": "BTC",
                    "time": "now",
                    "ok_mark": "✅",
                }
            ],
            "fixes_applied": ["canonical_probe"],
            "issues": [],
            "elapsed_sec": 1.0,
        })
        self.assertIn("RESEARCH CONSISTENCY AUDIT V1", text)
        self.assertIn("Fingerprint", text)
        self.assertIn("✅", text)


if __name__ == "__main__":
    unittest.main()
