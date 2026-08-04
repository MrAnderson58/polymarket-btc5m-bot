"""Batched Decision Journal inserts (research-only, no N+1)."""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    ALL_BOOKS,
    extract_module_signals,
    rejection_reasons,
    route_decision,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    JOURNAL_TABLE,
    ensure_decision_journal_schema,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.writer import (
    clear_journal,
    insert_journal_batch,
    replace_journal_atomic,
)


def _pnl(trade: dict[str, Any]) -> float | None:
    v = trade.get("pnl")
    if v is None:
        v = trade.get("pnl_pct")
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _result_label(trade: dict[str, Any], pnl: float | None) -> str:
    r = trade.get("result")
    if r in ("WIN", "LOSS", "BE", "OPEN"):
        return str(r)
    if pnl is None:
        return "UNKNOWN"
    if abs(pnl) < 1e-12:
        return "BE"
    return "WIN" if pnl > 0 else "LOSS"


def journal_row_from_decision(
    decision: dict[str, Any],
    trade: dict[str, Any],
    book: str,
    *,
    accepted: bool,
    decision_rank: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    sig = extract_module_signals(decision)
    pnl = _pnl(trade)
    reasons = list(decision.get("why") or decision.get("reasons") or [])
    if not accepted:
        reasons = rejection_reasons(decision, book) or reasons
    return {
        "trade_id": int(decision.get("trade_id") or trade.get("trade_id") or trade.get("id") or 0),
        "symbol": str(decision.get("symbol") or trade.get("symbol") or ""),
        "opened_at": int(trade.get("opened_at") or decision.get("opened_at") or 0) or None,
        "decision": str(decision.get("decision") or "NO TRADE"),
        "book": book,
        "accepted": 1 if accepted else 0,
        "direction": decision.get("direction") or trade.get("direction"),
        "confidence": sig.get("confidence"),
        "timeline_similarity": sig.get("timeline_similarity"),
        "fingerprint_similarity": sig.get("fingerprint_similarity"),
        "dna": sig.get("dna"),
        "rules": sig.get("rules"),
        "edge": sig.get("edge"),
        "replay": sig.get("replay"),
        "brain": sig.get("brain"),
        "causality": sig.get("causality"),
        "decision_rank": decision_rank,
        "reasons_json": json.dumps(reasons, ensure_ascii=False),
        "historical_wr": decision.get("historical_wr"),
        "historical_pf": (
            float(decision["historical_pf"])
            if isinstance(decision.get("historical_pf"), (int, float))
            else None
        ),
        "historical_ev": decision.get("historical_ev"),
        "result": _result_label(trade, pnl) if accepted else "REJECTED",
        "pnl": pnl if accepted else None,
        "created_at": int(now or time.time()),
        "_counterfactual_pnl": pnl,
        "_counterfactual_result": _result_label(trade, pnl),
    }


def build_journal_rows(
    decision: dict[str, Any],
    trade: dict[str, Any],
    *,
    decision_rank: str | None = None,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Build 3 journal rows (A/B/C) from one decision — no DB."""
    routing = route_decision(decision)
    rows = []
    for book in ALL_BOOKS:
        rows.append(
            journal_row_from_decision(
                decision,
                trade,
                book,
                accepted=bool(routing.get(book)),
                decision_rank=decision_rank,
                now=now,
            )
        )
    return rows


def load_journal_rows(
    conn: Any,
    *,
    book: str | None = None,
    accepted_only: bool = False,
    since: int | None = None,
    until: int | None = None,
) -> list[dict[str, Any]]:
    ensure_decision_journal_schema(conn)
    clauses = ["1=1"]
    args: list[Any] = []
    if book:
        clauses.append("book = ?")
        args.append(book)
    if accepted_only:
        clauses.append("accepted = 1")
    if since is not None:
        clauses.append("opened_at >= ?")
        args.append(int(since))
    if until is not None:
        clauses.append("opened_at < ?")
        args.append(int(until))
    sql = (
        f"SELECT * FROM {JOURNAL_TABLE} WHERE {' AND '.join(clauses)} "
        "ORDER BY opened_at ASC"
    )
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, args).fetchall():
        out.append(dict(row))
    return out


__all__ = [
    "build_journal_rows",
    "clear_journal",
    "insert_journal_batch",
    "journal_row_from_decision",
    "load_journal_rows",
    "replace_journal_atomic",
]
