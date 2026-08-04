"""test_book_report + false_reject_review."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.report import (
    format_book_comparison_md,
    format_book_terminal,
    format_decision_journal_md,
    format_paper_book_md,
    write_book_artifacts,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.review import (
    analyze_false_rejects,
    format_false_reject_md,
    format_review_terminal,
)


def _stats(**kwargs):
    base = {"trades": 10, "wr": 55.0, "pf": 1.2, "ev": 0.8, "sharpe": 0.5, "max_dd": -3.0}
    base.update(kwargs)
    return base


def _result():
    return {
        "ok": True,
        "elapsed_sec": 1.2,
        "n_journal": 30,
        "accepted": {"A": 10, "B": 4, "C": 2},
        "book_a": {"label": "BOOK A", "id": BOOK_A, **_stats(trades=10)},
        "book_b": {"label": "BOOK B", "id": BOOK_B, **_stats(trades=4, wr=70.0)},
        "book_c": {"label": "BOOK C", "id": BOOK_C, **_stats(trades=2, wr=80.0)},
        "overlap": {
            "a_and_b": 4,
            "a_and_c": 2,
            "b_and_c": 2,
            "filtered_b": 6,
            "filtered_c": 8,
        },
        "improvement_b": {"wr_delta": 15.0, "pf_delta": 0.3, "ev_delta": 0.2},
        "improvement_c": {"wr_delta": 25.0, "pf_delta": 0.5, "ev_delta": 0.4},
    }


class TestBookReport(unittest.TestCase):
    def test_terminal_has_three_books(self):
        text = format_book_terminal(_result())
        self.assertIn("BOOK A", text)
        self.assertIn("BOOK B", text)
        self.assertIn("BOOK C", text)

    def test_terminal_metrics(self):
        text = format_book_terminal(_result())
        for k in ("Trades", "WR", "PF", "EV", "Sharpe", "MaxDD"):
            self.assertIn(k, text)

    def test_terminal_overlap(self):
        text = format_book_terminal(_result())
        self.assertIn("A∩B", text)
        self.assertIn("A∩C", text)
        self.assertIn("B∩C", text)
        self.assertIn("Filtered", text)
        self.assertIn("Improvement", text)

    def test_terminal_separators(self):
        text = format_book_terminal(_result())
        self.assertGreaterEqual(text.count("===================="), 4)

    def test_paper_md(self):
        md = format_paper_book_md(_result())
        self.assertIn("# PAPER_BOOK_REPORT", md)
        self.assertIn("paper_baseline", md)
        self.assertIn("paper_decision", md)
        self.assertIn("paper_high_confidence", md)

    def test_comparison_md(self):
        md = format_book_comparison_md(_result())
        self.assertIn("# BOOK_COMPARISON", md)

    def test_journal_md(self):
        rows = [
            {
                "trade_id": 1,
                "book": BOOK_A,
                "accepted": 1,
                "decision": "TRADE",
                "confidence": 0.8,
                "result": "WIN",
                "pnl": 1.0,
            }
        ]
        md = format_decision_journal_md(rows, _result())
        self.assertIn("# DECISION_JOURNAL_REPORT", md)
        self.assertIn("| 1 |", md)

    def test_write_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch(
                "bot.research.market_events.signal_intelligence.paper_decision_books_v1.report.BASE_DIR",
                Path(td),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.paper_decision_books_v1.report.OUT_DIR",
                Path(td) / "out",
            ):
                paths = write_book_artifacts(_result(), [])
                self.assertTrue(Path(paths["paper_book_report"]).exists())
                self.assertTrue(Path(paths["book_comparison"]).exists())
                self.assertTrue(Path(paths["decision_journal_report"]).exists())

    def test_research_only_footer(self):
        text = format_book_terminal(_result())
        self.assertIn("research_only=true", text)


class TestFalseRejectReview(unittest.TestCase):
    def _rows(self):
        # trade 1: A win, B rejected → false reject
        # trade 2: A loss, B accepted → false approval
        # trade 3: A win, B accepted → ok
        return [
            {"trade_id": 1, "book": BOOK_A, "accepted": 1, "pnl": 5.0, "result": "WIN", "symbol": "BTC", "confidence": 0.4, "reasons_json": "[]"},
            {"trade_id": 1, "book": BOOK_B, "accepted": 0, "pnl": None, "result": "REJECTED", "symbol": "BTC", "confidence": 0.4, "reasons_json": json.dumps(["No historical edge"])},
            {"trade_id": 1, "book": BOOK_C, "accepted": 0, "pnl": None, "result": "REJECTED", "symbol": "BTC", "confidence": 0.4, "reasons_json": json.dumps(["confidence<0.70"])},
            {"trade_id": 2, "book": BOOK_A, "accepted": 1, "pnl": -3.0, "result": "LOSS", "symbol": "ETH", "confidence": 0.9, "reasons_json": "[]"},
            {"trade_id": 2, "book": BOOK_B, "accepted": 1, "pnl": -3.0, "result": "LOSS", "symbol": "ETH", "confidence": 0.9, "reasons_json": json.dumps(["ok"])},
            {"trade_id": 2, "book": BOOK_C, "accepted": 0, "pnl": None, "result": "REJECTED", "symbol": "ETH", "confidence": 0.9, "reasons_json": json.dumps(["timeline_similarity<0.70"])},
            {"trade_id": 3, "book": BOOK_A, "accepted": 1, "pnl": 2.0, "result": "WIN", "symbol": "ADA", "confidence": 0.85, "reasons_json": "[]"},
            {"trade_id": 3, "book": BOOK_B, "accepted": 1, "pnl": 2.0, "result": "WIN", "symbol": "ADA", "confidence": 0.85, "reasons_json": json.dumps(["ok"])},
            {"trade_id": 3, "book": BOOK_C, "accepted": 1, "pnl": 2.0, "result": "WIN", "symbol": "ADA", "confidence": 0.85, "reasons_json": json.dumps(["ok"])},
        ]

    def test_rejected_count(self):
        a = analyze_false_rejects(self._rows())
        self.assertEqual(a["rejected_trades"], 1)

    def test_false_rejects(self):
        a = analyze_false_rejects(self._rows())
        self.assertEqual(a["n_false_rejects"], 1)
        self.assertEqual(a["top_false_rejects"][0]["trade_id"], 1)

    def test_false_approvals(self):
        a = analyze_false_rejects(self._rows())
        self.assertEqual(a["n_false_approvals"], 1)
        self.assertEqual(a["top_false_approvals"][0]["trade_id"], 2)

    def test_most_profitable_rejected(self):
        a = analyze_false_rejects(self._rows())
        self.assertEqual(a["most_profitable_rejected_setup"]["trade_id"], 1)
        self.assertEqual(a["most_profitable_rejected_setup"]["pnl"], 5.0)

    def test_most_expensive_mistake(self):
        a = analyze_false_rejects(self._rows())
        self.assertEqual(a["most_expensive_accepted_mistake"]["trade_id"], 2)

    def test_top_reasons(self):
        a = analyze_false_rejects(self._rows())
        self.assertTrue(a["top_rejection_reasons"])

    def test_review_terminal(self):
        a = analyze_false_rejects(self._rows())
        a["elapsed_sec"] = 0.1
        text = format_review_terminal(a)
        self.assertIn("Rejected trades", text)
        self.assertIn("Top rejection reasons", text)
        self.assertIn("Top false rejects", text)
        self.assertIn("Top false approvals", text)
        self.assertIn("Most profitable rejected setup", text)
        self.assertIn("Most expensive accepted mistake", text)

    def test_false_reject_md(self):
        a = analyze_false_rejects(self._rows())
        md = format_false_reject_md(a)
        self.assertIn("# FALSE_REJECT_REPORT", md)

    def test_empty_rows(self):
        a = analyze_false_rejects([])
        self.assertEqual(a["rejected_trades"], 0)
        self.assertIsNone(a["most_profitable_rejected_setup"])


if __name__ == "__main__":
    unittest.main()
