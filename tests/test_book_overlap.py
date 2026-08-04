"""test_book_overlap."""

from __future__ import annotations

import unittest

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    book_overlap,
    trade_id_set,
)


class TestBookOverlap(unittest.TestCase):
    def test_intersection(self):
        a = [{"book": BOOK_A, "trade_id": i, "accepted": 1} for i in (1, 2, 3)]
        b = [{"book": BOOK_B, "trade_id": i, "accepted": 1} for i in (2, 3)]
        c = [{"book": BOOK_C, "trade_id": i, "accepted": 1} for i in (3,)]
        ov = book_overlap(a, b, c)
        self.assertEqual(ov["a_and_b"], 2)
        self.assertEqual(ov["a_and_c"], 1)
        self.assertEqual(ov["b_and_c"], 1)

    def test_filtered(self):
        a = [{"book": BOOK_A, "trade_id": i, "accepted": 1} for i in range(1, 6)]
        b = [{"book": BOOK_B, "trade_id": i, "accepted": 1} for i in (1, 2)]
        c = [{"book": BOOK_C, "trade_id": i, "accepted": 1} for i in (1,)]
        ov = book_overlap(a, b, c)
        self.assertEqual(ov["filtered_b"], 3)
        self.assertEqual(ov["filtered_c"], 4)

    def test_set_ignores_rejected(self):
        rows = [
            {"trade_id": 1, "accepted": 1},
            {"trade_id": 2, "accepted": 0},
        ]
        self.assertEqual(trade_id_set(rows), {1})

    def test_empty(self):
        self.assertEqual(book_overlap([], [], [])["n_a"], 0)


if __name__ == "__main__":
    unittest.main()
