"""Load lake + journal once; build ordered feature rows (no N+1)."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import BOOK_B
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    JOURNAL_TABLE,
    ensure_decision_journal_schema,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    load_research_lake_rows,
)


def _load_journal_by_trade(conn: Any) -> dict[int, dict[str, Any]]:
    try:
        ensure_decision_journal_schema(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM {JOURNAL_TABLE}
            WHERE book = ? AND accepted = 1
            """,
            (BOOK_B,),
        ).fetchall()
    except Exception:
        try:
            rows = conn.execute(f"SELECT * FROM {JOURNAL_TABLE}").fetchall()
        except Exception:
            return {}
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        tid = int(d.get("trade_id") or 0)
        if not tid:
            continue
        # Prefer Book B; otherwise first seen
        if tid not in out or d.get("book") == BOOK_B:
            out[tid] = d
    return out


def build_ordered_corpus(
    conn: Any,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Flattened lake rows sorted by closed_at, with optional journal merge."""
    lake = load_research_lake_rows(conn, limit=limit)
    journal = _load_journal_by_trade(conn)
    rows: list[dict[str, Any]] = []
    for r in lake:
        tid = int(r.get("trade_id") or r.get("id") or 0)
        item = dict(r)
        # features already flattened by loader
        j = journal.get(tid)
        if j:
            item["_journal"] = j
        rows.append(item)
    rows.sort(key=lambda x: int(x.get("closed_at") or x.get("opened_at") or 0))
    return rows


__all__ = ["build_ordered_corpus"]
