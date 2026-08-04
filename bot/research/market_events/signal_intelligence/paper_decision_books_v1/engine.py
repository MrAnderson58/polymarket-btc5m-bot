"""Paper Decision Books A/B/C engine — research-only forward validation."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.market_decision_v1.context import (
    build_decision_context,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    decide_one,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.explain import (
    decision_rank,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    build_journal_rows,
    load_journal_rows,
    replace_journal_atomic,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    book_overlap,
    book_stats_from_rows,
    improvement,
    partition_by_book,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)


def run_paper_decision_books_v1(
    conn: Any,
    *,
    limit: int | None = None,
    write_reports: bool = True,
    rebuild: bool = True,
) -> dict[str, Any]:
    """
    Route Research Lake candidates into Book A/B/C and journal every decision.
    Does not modify Gate / Strategy / Optimizer / Paper / Live execution.
    """
    t0 = time.time()
    ensure_decision_journal_schema(conn)
    ctx = build_decision_context(conn, limit=limit)
    if not ctx.get("ok"):
        return {
            "ok": False,
            "error": "empty_context",
            "terminal": "PAPER DECISION BOOKS V1\n\nERROR empty corpus",
            "research_only": True,
        }

    trades = list(ctx.get("trades") or [])
    all_rows: list[dict[str, Any]] = []
    decide_ms: list[float] = []
    n_a = n_b = n_c = 0

    for trade in trades:
        t1 = time.perf_counter()
        d = decide_one(ctx, trade)
        decide_ms.append((time.perf_counter() - t1) * 1000.0)
        rank = decision_rank(d)
        rows = build_journal_rows(d, trade, decision_rank=rank)
        for r in rows:
            if int(r.get("accepted") or 0) == 1:
                if r["book"] == BOOK_A:
                    n_a += 1
                elif r["book"] == BOOK_B:
                    n_b += 1
                elif r["book"] == BOOK_C:
                    n_c += 1
        all_rows.extend(rows)

    if rebuild:
        replace_journal_atomic(conn, all_rows)
    else:
        from bot.research.market_events.signal_intelligence.paper_decision_books_v1.writer import (
            insert_journal_batch,
        )
        from bot.research.market_events.research_db_session import research_write_lock
        import threading

        with threading.RLock():
            with research_write_lock():
                try:
                    conn.execute("BEGIN IMMEDIATE")
                except Exception:
                    pass
                insert_journal_batch(conn, all_rows, commit=False, ensure_schema=False)
                conn.commit()

    loaded = load_journal_rows(conn)
    by_book = partition_by_book(loaded)
    stats_a = book_stats_from_rows(by_book[BOOK_A])
    stats_b = book_stats_from_rows(by_book[BOOK_B])
    stats_c = book_stats_from_rows(by_book[BOOK_C])
    overlap = book_overlap(by_book[BOOK_A], by_book[BOOK_B], by_book[BOOK_C])
    improv_b = improvement(stats_a, stats_b)
    improv_c = improvement(stats_a, stats_c)

    avg_decide = round(sum(decide_ms) / len(decide_ms), 3) if decide_ms else 0.0
    p99_decide = (
        round(sorted(decide_ms)[int(0.99 * (len(decide_ms) - 1))], 3) if decide_ms else 0.0
    )
    elapsed = round(time.time() - t0, 3)

    result = {
        "ok": True,
        "research_only": True,
        "n_candidates": len(trades),
        "n_journal": len(loaded),
        "accepted": {"A": n_a, "B": n_b, "C": n_c},
        "book_a": {"label": "BOOK A", "id": BOOK_A, **stats_a},
        "book_b": {"label": "BOOK B", "id": BOOK_B, **stats_b},
        "book_c": {"label": "BOOK C", "id": BOOK_C, **stats_c},
        "overlap": overlap,
        "improvement_b": improv_b,
        "improvement_c": improv_c,
        "perf": {
            "avg_decide_ms": avg_decide,
            "p99_decide_ms": p99_decide,
            "n_decisions": len(decide_ms),
            "single_writer": True,
        },
        "elapsed_sec": elapsed,
        "gate_unchanged": True,
        "strategy_unchanged": True,
        "optimizer_unchanged": True,
        "paper_execution_unchanged": True,
        "live_execution_unchanged": True,
    }

    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.report import (
        format_book_terminal,
        write_book_artifacts,
    )

    result["terminal"] = format_book_terminal(result)
    if write_reports:
        paths = write_book_artifacts(result, loaded)
        result["paths"] = paths
    return result


def run_paper_book_report(
    conn: Any,
    *,
    write_reports: bool = True,
) -> dict[str, Any]:
    """Daily comparison from journal (read-only; no rebuild)."""
    t0 = time.time()
    ensure_decision_journal_schema(conn)
    all_rows = load_journal_rows(conn)
    if not all_rows:
        return {
            "ok": False,
            "error": "empty_journal",
            "terminal": (
                "PAPER DECISION BOOKS V1\n\n"
                "ERROR empty journal — run: paper-decision-books"
            ),
            "research_only": True,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    by_book = partition_by_book(all_rows)
    stats_a = book_stats_from_rows(by_book[BOOK_A])
    stats_b = book_stats_from_rows(by_book[BOOK_B])
    stats_c = book_stats_from_rows(by_book[BOOK_C])
    overlap = book_overlap(by_book[BOOK_A], by_book[BOOK_B], by_book[BOOK_C])
    result = {
        "ok": True,
        "research_only": True,
        "n_journal": len(all_rows),
        "book_a": {"label": "BOOK A", "id": BOOK_A, **stats_a},
        "book_b": {"label": "BOOK B", "id": BOOK_B, **stats_b},
        "book_c": {"label": "BOOK C", "id": BOOK_C, **stats_c},
        "overlap": overlap,
        "improvement_b": improvement(stats_a, stats_b),
        "improvement_c": improvement(stats_a, stats_c),
        "elapsed_sec": round(time.time() - t0, 3),
    }
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.report import (
        format_book_terminal,
        write_book_artifacts,
    )

    result["terminal"] = format_book_terminal(result)
    if write_reports:
        result["paths"] = write_book_artifacts(result, all_rows)
    return result


__all__ = ["run_paper_book_report", "run_paper_decision_books_v1"]
