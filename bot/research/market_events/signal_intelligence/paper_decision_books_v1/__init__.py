"""Market Paper Decision A/B/C V1 — research-only forward validation books."""

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
    route_decision,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.engine import (
    run_paper_book_report,
    run_paper_decision_books_v1,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.review import (
    run_decision_review,
)

__all__ = [
    "BOOK_A",
    "BOOK_B",
    "BOOK_C",
    "route_decision",
    "run_decision_review",
    "run_paper_book_report",
    "run_paper_decision_books_v1",
]
