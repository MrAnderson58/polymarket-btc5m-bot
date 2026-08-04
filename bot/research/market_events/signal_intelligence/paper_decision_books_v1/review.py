"""False reject / false approval analysis for Decision Books."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "paper_decision_books_v1"


def _reasons(row: dict[str, Any]) -> list[str]:
    raw = row.get("reasons_json") or "[]"
    try:
        data = json.loads(raw) if isinstance(raw, str) else list(raw)
        return [str(x) for x in data]
    except Exception:
        return [str(raw)]


def _by_trade(rows: list[dict[str, Any]], book: str) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        if str(r.get("book") or "") != book:
            continue
        tid = int(r.get("trade_id") or 0)
        if tid:
            out[tid] = r
    return out


def analyze_false_rejects(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare Book A outcomes vs B/C filters."""
    a = _by_trade(rows, BOOK_A)
    b = _by_trade(rows, BOOK_B)
    c = _by_trade(rows, BOOK_C)

    rejected_b: list[dict[str, Any]] = []
    rejected_c: list[dict[str, Any]] = []
    false_rejects: list[dict[str, Any]] = []
    false_approvals: list[dict[str, Any]] = []
    reason_counter: Counter[str] = Counter()

    for tid, ar in a.items():
        br = b.get(tid) or {}
        cr = c.get(tid) or {}
        a_pnl = ar.get("pnl")
        try:
            a_pnl_f = float(a_pnl) if a_pnl is not None else None
        except Exception:
            a_pnl_f = None
        a_win = a_pnl_f is not None and a_pnl_f > 0
        a_loss = a_pnl_f is not None and a_pnl_f < 0

        if int(br.get("accepted") or 0) == 0:
            item = {
                "trade_id": tid,
                "symbol": ar.get("symbol"),
                "book": BOOK_B,
                "pnl": a_pnl_f,
                "result": ar.get("result"),
                "reasons": _reasons(br) if br else ["NO TRADE"],
                "confidence": br.get("confidence") or ar.get("confidence"),
            }
            rejected_b.append(item)
            for rr in item["reasons"]:
                reason_counter[rr] += 1
            if a_win:
                false_rejects.append(item)

        if int(br.get("accepted") or 0) == 1 and a_loss:
            false_approvals.append({
                "trade_id": tid,
                "symbol": ar.get("symbol"),
                "book": BOOK_B,
                "pnl": a_pnl_f,
                "result": ar.get("result"),
                "confidence": br.get("confidence"),
                "reasons": _reasons(br),
            })

        if int(cr.get("accepted") or 0) == 0:
            item_c = {
                "trade_id": tid,
                "symbol": ar.get("symbol"),
                "book": BOOK_C,
                "pnl": a_pnl_f,
                "result": ar.get("result"),
                "reasons": _reasons(cr) if cr else ["filtered"],
                "confidence": cr.get("confidence") or ar.get("confidence"),
            }
            rejected_c.append(item_c)

    false_rejects_sorted = sorted(
        false_rejects, key=lambda x: float(x.get("pnl") or 0), reverse=True
    )
    false_approvals_sorted = sorted(
        false_approvals, key=lambda x: float(x.get("pnl") or 0)
    )
    most_profitable_rejected = false_rejects_sorted[0] if false_rejects_sorted else None
    most_expensive_mistake = false_approvals_sorted[0] if false_approvals_sorted else None

    return {
        "rejected_trades": len(rejected_b),
        "rejected_b": rejected_b[:50],
        "rejected_c_n": len(rejected_c),
        "top_rejection_reasons": reason_counter.most_common(15),
        "top_false_rejects": false_rejects_sorted[:10],
        "top_false_approvals": false_approvals_sorted[:10],
        "most_profitable_rejected_setup": most_profitable_rejected,
        "most_expensive_accepted_mistake": most_expensive_mistake,
        "n_false_rejects": len(false_rejects),
        "n_false_approvals": len(false_approvals),
    }


def format_review_terminal(analysis: dict[str, Any]) -> str:
    lines = [
        "DECISION REVIEW V1",
        "",
        "Rejected trades",
        f"  {analysis.get('rejected_trades')}",
        "",
        "Top rejection reasons",
    ]
    reasons = analysis.get("top_rejection_reasons") or []
    if not reasons:
        lines.append("  (none)")
    else:
        for reason, n in reasons[:10]:
            lines.append(f"  {n}× {reason}")
    lines.extend(["", "Top false rejects"])
    fr = analysis.get("top_false_rejects") or []
    if not fr:
        lines.append("  (none)")
    else:
        for r in fr[:5]:
            lines.append(
                f"  #{r.get('trade_id')} {r.get('symbol')} pnl={r.get('pnl')} "
                f"{', '.join((r.get('reasons') or [])[:2])}"
            )
    lines.extend(["", "Top false approvals"])
    fa = analysis.get("top_false_approvals") or []
    if not fa:
        lines.append("  (none)")
    else:
        for r in fa[:5]:
            lines.append(f"  #{r.get('trade_id')} {r.get('symbol')} pnl={r.get('pnl')}")
    mpr = analysis.get("most_profitable_rejected_setup") or {}
    mem = analysis.get("most_expensive_accepted_mistake") or {}
    lines.extend(
        [
            "",
            "Most profitable rejected setup",
            f"  #{mpr.get('trade_id') or '—'} pnl={mpr.get('pnl')}",
            "",
            "Most expensive accepted mistake",
            f"  #{mem.get('trade_id') or '—'} pnl={mem.get('pnl')}",
            "",
            f"elapsed={analysis.get('elapsed_sec')}s research_only=true",
        ]
    )
    return "\n".join(lines)


def format_false_reject_md(analysis: dict[str, Any]) -> str:
    lines = [
        "# FALSE_REJECT_REPORT",
        "",
        "_Decision Review V1 — research only._",
        "",
        f"- rejected (Book B): {analysis.get('rejected_trades')}",
        f"- false rejects: {analysis.get('n_false_rejects')}",
        f"- false approvals: {analysis.get('n_false_approvals')}",
        "",
        "## Top rejection reasons",
        "",
    ]
    for reason, n in analysis.get("top_rejection_reasons") or []:
        lines.append(f"- {n}× `{reason}`")
    lines.extend(["", "## Top false rejects", ""])
    for r in analysis.get("top_false_rejects") or []:
        lines.append(f"- #{r.get('trade_id')} {r.get('symbol')} pnl={r.get('pnl')} reasons={r.get('reasons')}")
    lines.extend(["", "## Top false approvals", ""])
    for r in analysis.get("top_false_approvals") or []:
        lines.append(f"- #{r.get('trade_id')} {r.get('symbol')} pnl={r.get('pnl')}")
    mpr = analysis.get("most_profitable_rejected_setup")
    mem = analysis.get("most_expensive_accepted_mistake")
    lines.extend(
        [
            "",
            "## Most profitable rejected setup",
            f"- `{mpr}`",
            "",
            "## Most expensive accepted mistake",
            f"- `{mem}`",
            "",
        ]
    )
    return "\n".join(lines)


def run_decision_review(
    conn: Any,
    *,
    write_reports: bool = True,
) -> dict[str, Any]:
    import time

    t0 = time.time()
    ensure_decision_journal_schema(conn)
    rows = load_journal_rows(conn)
    if not rows:
        return {
            "ok": False,
            "error": "empty_journal",
            "terminal": (
                "DECISION REVIEW V1\n\n"
                "ERROR empty journal — run: paper-decision-books"
            ),
            "research_only": True,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    analysis = analyze_false_rejects(rows)
    analysis["ok"] = True
    analysis["research_only"] = True
    analysis["elapsed_sec"] = round(time.time() - t0, 3)
    analysis["terminal"] = format_review_terminal(analysis)

    if write_reports:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        md = format_false_reject_md(analysis)
        root = BASE_DIR / "FALSE_REJECT_REPORT.md"
        root.write_text(md, encoding="utf-8")
        (OUT_DIR / "FALSE_REJECT_REPORT.md").write_text(md, encoding="utf-8")
        analysis["paths"] = {
            "false_reject_report": str(root),
            "out": str(OUT_DIR / "FALSE_REJECT_REPORT.md"),
        }
    return analysis


__all__ = [
    "analyze_false_rejects",
    "format_false_reject_md",
    "format_review_terminal",
    "run_decision_review",
]
