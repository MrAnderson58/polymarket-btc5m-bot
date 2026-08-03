"""Tests for Decision Explainability Engine V1."""

from __future__ import annotations

import unittest

from bot.research.market_events.signal_intelligence.market_decision_v1.explain import (
    decision_rank,
    explain_from_decision,
    format_explain,
)


def _trade_result(**kwargs):
    base = {
        "ok": True,
        "trade_id": 18492,
        "decision": "TRADE",
        "direction": "SHORT",
        "confidence": 0.94,
        "confidence_pct": 94.0,
        "historical_wr": 81.0,
        "historical_pf": 2.74,
        "historical_ev": 16.8,
        "supporting_modules": 5,
        "why": ["Fingerprint 88%", "Confidence 94%"],
        "scores": {
            "replay": {"pass": True, "similarity_pct": 91.0, "ok": True},
            "fingerprint": {"pass": True, "similarity_pct": 88.0, "ok": True},
            "timeline": {"pass": True, "similarity_pct": 79.0, "ok": True},
            "dna": {"pass": True, "ok": True},
            "rules": {"pass": True, "rule": "Rule #4", "ok": True},
            "edge": {"pass": True, "ok": True},
            "causality": {"pass": False, "ok": False},
            "brain": {"pass": False, "brain_decision": "HOLD", "ok": True},
        },
        "recommendation": "RESEARCH ONLY",
    }
    base.update(kwargs)
    return base


class TestExplain(unittest.TestCase):
    def test_trade_card(self):
        r = explain_from_decision(_trade_result())
        self.assertEqual(r["decision_rank"], "A+")
        text = r["explain_text"]
        self.assertIn("TRADE #18492", text)
        self.assertIn("TRADE SHORT", text)
        self.assertIn("Replay", text)
        self.assertIn("✔ 0.91", text)
        self.assertIn("Fingerprint", text)
        self.assertIn("✔ 0.88", text)
        self.assertIn("Rule #4", text)
        self.assertIn("Brain", text)
        self.assertIn("✖", text)
        self.assertIn("Decision Rank", text)
        self.assertIn("A+", text)
        self.assertIn("Why accepted", text)

    def test_no_trade_card(self):
        r = explain_from_decision(
            _trade_result(
                decision="NO TRADE",
                direction=None,
                confidence=0.31,
                confidence_pct=31.0,
                historical_wr=42.0,
                supporting_modules=1,
                why=[
                    "Replay unknown",
                    "No DNA",
                    "Fingerprint mismatch",
                    "Historical WR 42%",
                    "Confidence 31%",
                ],
                scores={
                    "replay": {"pass": False, "ok": False, "reason": "Replay unknown"},
                    "fingerprint": {"pass": False, "ok": True, "similarity_pct": 7.0},
                    "timeline": {"pass": False, "ok": True},
                    "dna": {"pass": False, "ok": True},
                    "rules": {"pass": False, "ok": True},
                    "edge": {"pass": False, "ok": True},
                    "causality": {"pass": False, "ok": False},
                    "brain": {"pass": False, "ok": True},
                },
            )
        )
        self.assertIn(r["decision_rank"], ("C", "D"))
        text = format_explain(r)
        self.assertIn("NO TRADE", text)
        self.assertIn("Reasons", text)
        self.assertIn("Replay unknown", text)
        self.assertIn("Confidence 31%", text)


if __name__ == "__main__":
    unittest.main()
