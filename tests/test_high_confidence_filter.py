"""test_high_confidence_filter — Book C gates."""

from __future__ import annotations

import unittest

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    C_MIN_CONFIDENCE,
    C_MIN_FINGERPRINT_SIM,
    C_MIN_RULES_MATCHED,
    C_MIN_TIMELINE_SIM,
    book_c_accepts,
    rejection_reasons,
    BOOK_C,
)


def _d(*, trade=True, conf=0.8, tl=80.0, fp=70.0, rules=True):
    return {
        "decision": "TRADE" if trade else "NO TRADE",
        "confidence": conf,
        "why": ["x"],
        "scores": {
            "fingerprint": {"pass": True, "similarity_pct": fp, "ok": True},
            "timeline": {"pass": True, "similarity_pct": tl, "ok": True},
            "rules": {"pass": rules, "blocked": False, "ok": True},
            "dna": {"pass": True, "similarity_pct": 70, "ok": True},
            "edge": {"pass": True, "similarity_pct": 70, "ok": True},
            "replay": {"pass": True, "similarity_pct": 70, "ok": True},
            "brain": {"pass": True, "confidence": conf, "ok": True},
            "causality": {"pass": True, "similarity_pct": 70, "ok": True},
        },
    }


class TestHighConfidenceFilter(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(book_c_accepts(_d()))

    def test_fail_trade(self):
        self.assertFalse(book_c_accepts(_d(trade=False)))

    def test_fail_conf(self):
        self.assertFalse(book_c_accepts(_d(conf=0.69)))

    def test_fail_tl(self):
        self.assertFalse(book_c_accepts(_d(tl=69)))

    def test_fail_fp(self):
        self.assertFalse(book_c_accepts(_d(fp=59)))

    def test_fail_rules(self):
        self.assertFalse(book_c_accepts(_d(rules=False)))

    def test_constants(self):
        self.assertEqual(C_MIN_CONFIDENCE, 0.70)
        self.assertEqual(C_MIN_TIMELINE_SIM, 0.70)
        self.assertEqual(C_MIN_FINGERPRINT_SIM, 0.60)
        self.assertEqual(C_MIN_RULES_MATCHED, 1)

    def test_reasons(self):
        rs = rejection_reasons(_d(conf=0.5, rules=False), BOOK_C)
        self.assertGreaterEqual(len(rs), 2)


if __name__ == "__main__":
    unittest.main()
