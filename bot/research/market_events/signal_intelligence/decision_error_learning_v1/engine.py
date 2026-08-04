"""Decision Error Learning Engine V1 orchestrator."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from bot.research.market_events.signal_intelligence.decision_error_learning_v1.classify import (
    classify_trade,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.modules import (
    adaptive_suggestions,
    module_error_ranking,
    recovered_if_module_ignored,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.patterns import (
    mine_patterns,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.schema import (
    ensure_decision_error_schema,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.store import (
    persist_errors,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    partition_by_book,
)


def _index_by_trade(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        tid = int(r.get("trade_id") or 0)
        if tid:
            out[tid] = r
    return out


def run_decision_error_learning_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
    book: str = BOOK_B,
) -> dict[str, Any]:
    """
    Learn Decision Engine mistakes from Decision Journal books A/B/C.
    Research-only — does not change thresholds or trading paths.
    """
    t0 = time.time()
    ensure_decision_error_schema(conn)
    rows = load_journal_rows(conn)
    if not rows:
        return {
            "ok": False,
            "error": "empty_journal",
            "terminal": (
                "DECISION ERROR LEARNING ENGINE V1\n\n"
                "ERROR empty journal — run: paper-decision-books"
            ),
            "research_only": True,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    by_book = partition_by_book(rows)
    a = _index_by_trade(by_book.get(BOOK_A) or [])
    b = _index_by_trade(by_book.get(book) or by_book.get(BOOK_B) or [])
    c = _index_by_trade(by_book.get(BOOK_C) or [])

    records: list[dict[str, Any]] = []
    for tid, ar in a.items():
        br = b.get(tid)
        if not br:
            continue
        rec = classify_trade(ar, br)
        # annotate recovered EV for primary module (per-trade hint)
        mod = rec.get("primary_module") or "unknown"
        if rec.get("confusion") == "FN" and rec.get("pnl") is not None:
            rec["recovered_ev_if_ignored"] = float(rec["pnl"])
        else:
            rec["recovered_ev_if_ignored"] = None
        # Book C overlay flag
        cr = c.get(tid)
        if cr is not None:
            rec["book_c_accepted"] = int(cr.get("accepted") or 0) == 1
        records.append(rec)

    confusion = Counter(r["confusion"] for r in records)
    class_counts = Counter(r["error_class"] for r in records)

    fr_patterns = mine_patterns(records, kind="false_reject", top_n=100)
    fa_patterns = mine_patterns(records, kind="false_accept", top_n=100)
    ranking = module_error_ranking(records)
    suggestions = adaptive_suggestions(ranking)

    largest = ranking[0] if ranking else {}
    # Best = lowest error score among modules that actually participated (not unused zeros).
    active = [
        r for r in ranking
        if int(r.get("false_reject_n") or 0) + int(r.get("false_accept_n") or 0) > 0
    ]
    best = (
        min(active, key=lambda r: float(r.get("error_score") or 0))
        if active
        else (min(ranking, key=lambda r: float(r.get("error_score") or 0)) if ranking else {})
    )
    recovered = recovered_if_module_ignored(records, largest.get("module") or "unknown")

    stored = {"errors": 0, "patterns": 0}
    if persist:
        stored = persist_errors(
            conn,
            records=records,
            patterns=fr_patterns + fa_patterns,
            book=book,
        )

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "n": len(records),
        "confusion": dict(confusion),
        "error_classes": dict(class_counts),
        "false_reject_patterns": fr_patterns,
        "false_accept_patterns": fa_patterns,
        "module_ranking": ranking,
        "suggestions": suggestions,
        "largest_error_source": largest,
        "best_module": best,
        "recovered_ev_summary": recovered,
        "stored": stored,
        "elapsed_sec": elapsed,
        "gate_unchanged": True,
        "strategy_unchanged": True,
        "execution_unchanged": True,
        "paper_unchanged": True,
        "optimizer_unchanged": True,
        "brain_unchanged": True,
        "decision_thresholds_unchanged": True,
    }
    result["terminal"] = format_terminal(result)
    if write_reports:
        result["paths"] = write_artifacts(result)
    return result


def run_decision_error_report(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    return run_decision_error_learning_v1(conn, write_reports=write_reports, persist=False)


def run_decision_error_review(conn: Any) -> dict[str, Any]:
    """Compact review: top FR/FA + module ranking."""
    out = run_decision_error_learning_v1(conn, write_reports=False, persist=False)
    if not out.get("ok"):
        return out
    lines = [
        "DECISION ERROR REVIEW V1",
        "",
        f"Confusion {out.get('confusion')}",
        "",
        "Top false rejects",
    ]
    for p in (out.get("false_reject_patterns") or [])[:10]:
        lines.append(
            f"  n={p.get('n')} pnl={p.get('total_pnl')} mod={p.get('primary_module')} "
            f"{(p.get('sample_reasons') or [])[:2]}"
        )
    lines.extend(["", "Top false accepts"])
    for p in (out.get("false_accept_patterns") or [])[:10]:
        lines.append(
            f"  n={p.get('n')} pnl={p.get('total_pnl')} mod={p.get('primary_module')}"
        )
    largest = out.get("largest_error_source") or {}
    lines.extend([
        "",
        "Largest error source",
        f"  {largest.get('module')} recovered_EV={largest.get('recovered_ev')} "
        f"total={largest.get('recovered_total_pnl')}",
        "",
        f"elapsed={out.get('elapsed_sec')}s research_only=true",
    ])
    out["terminal"] = "\n".join(lines)
    return out


__all__ = [
    "run_decision_error_learning_v1",
    "run_decision_error_report",
    "run_decision_error_review",
]
