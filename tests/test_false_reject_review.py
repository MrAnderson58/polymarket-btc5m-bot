"""test_false_reject_review."""

from __future__ import annotations

import json
import unittest

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.review import (
    analyze_false_rejects,
    format_false_reject_md,
    format_review_terminal,
)


class TestFalseRejectReview(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"trade_id": 10, "book": BOOK_A, "accepted": 1, "pnl": 4.0, "result": "WIN", "symbol": "BTC", "confidence": 0.3, "reasons_json": "[]"},
            {"trade_id": 10, "book": BOOK_B, "accepted": 0, "pnl": None, "result": "REJECTED", "symbol": "BTC", "confidence": 0.3, "reasons_json": json.dumps(["Confidence low"])},
            {"trade_id": 10, "book": BOOK_C, "accepted": 0, "pnl": None, "result": "REJECTED", "symbol": "BTC", "confidence": 0.3, "reasons_json": json.dumps(["confidence<0.70"])},
            {"trade_id": 11, "book": BOOK_A, "accepted": 1, "pnl": -5.0, "result": "LOSS", "symbol": "ETH", "confidence": 0.9, "reasons_json": "[]"},
            {"trade_id": 11, "book": BOOK_B, "accepted": 1, "pnl": -5.0, "result": "LOSS", "symbol": "ETH", "confidence": 0.9, "reasons_json": json.dumps(["ok"])},
            {"trade_id": 11, "book": BOOK_C, "accepted": 0, "pnl": None, "result": "REJECTED", "symbol": "ETH", "confidence": 0.9, "reasons_json": json.dumps(["rules_matched<1"])},
        ]

    def test_counts(self):
        a = analyze_false_rejects(self.rows)
        self.assertEqual(a["n_false_rejects"], 1)
        self.assertEqual(a["n_false_approvals"], 1)

    def test_best_rejected(self):
        a = analyze_false_rejects(self.rows)
        self.assertEqual(a["most_profitable_rejected_setup"]["pnl"], 4.0)

    def test_worst_approval(self):
        a = analyze_false_rejects(self.rows)
        self.assertEqual(a["most_expensive_accepted_mistake"]["pnl"], -5.0)

    def test_terminal(self):
        a = analyze_false_rejects(self.rows)
        a["elapsed_sec"] = 0.01
        text = format_review_terminal(a)
        self.assertIn("DECISION REVIEW V1", text)

    def test_md(self):
        self.assertIn("FALSE_REJECT_REPORT", format_false_reject_md(analyze_false_rejects(self.rows)))


if __name__ == "__main__":
    unittest.main()
