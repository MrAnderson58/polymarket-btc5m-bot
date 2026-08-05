"""Book C / Book D identifiers for Paper Mathematics Validation V1."""

from __future__ import annotations

# Existing journal books (consume only)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
)

# Mathematical paper books (owned by this engine — research only)
BOOK_C = "paper_math_elite"       # Mathematical Elite
BOOK_D = "paper_strict_math"      # Strict Mathematics (reference)

BOOK_LABELS = {
    BOOK_A: "Book A",
    BOOK_B: "Book B",
    BOOK_C: "Book C (Mathematical Elite)",
    BOOK_D: "Book D (Strict Mathematics)",
}

MATH_BOOKS = (BOOK_C, BOOK_D)
ALL_COMPARE_BOOKS = (BOOK_A, BOOK_B, BOOK_C, BOOK_D)

__all__ = [
    "ALL_COMPARE_BOOKS",
    "BOOK_A",
    "BOOK_B",
    "BOOK_C",
    "BOOK_D",
    "BOOK_LABELS",
    "MATH_BOOKS",
]
