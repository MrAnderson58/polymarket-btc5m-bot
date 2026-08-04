"""Terminal + markdown reports for Paper Decision Books A/B/C."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "paper_decision_books_v1"


def _pf(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def _book_block(label: str, stats: dict[str, Any]) -> list[str]:
    return [
        "====================",
        "",
        label,
        "",
        f"Trades  {stats.get('trades') or 0}",
        f"WR      {stats.get('wr')}",
        f"PF      {_pf(stats.get('pf'))}",
        f"EV      {stats.get('ev')}",
        f"Sharpe  {stats.get('sharpe')}",
        f"MaxDD   {stats.get('max_dd')}",
        "",
    ]


def format_book_terminal(result: dict[str, Any]) -> str:
    a = result.get("book_a") or {}
    b = result.get("book_b") or {}
    c = result.get("book_c") or {}
    ov = result.get("overlap") or {}
    ib = result.get("improvement_b") or {}
    ic = result.get("improvement_c") or {}
    lines: list[str] = ["PAPER DECISION BOOKS V1", ""]
    lines.extend(_book_block("BOOK A", a))
    lines.extend(_book_block("BOOK B", b))
    lines.extend(_book_block("BOOK C", c))
    lines.extend(
        [
            "====================",
            "",
            "Overlap",
            "",
            f"A∩B  {ov.get('a_and_b')}",
            f"A∩C  {ov.get('a_and_c')}",
            f"B∩C  {ov.get('b_and_c')}",
            "",
            "Filtered",
            f"  A−B  {ov.get('filtered_b')}",
            f"  A−C  {ov.get('filtered_c')}",
            "",
            "Improvement",
            f"  B vs A  WRΔ={ib.get('wr_delta')} PFΔ={ib.get('pf_delta')} EVΔ={ib.get('ev_delta')}",
            f"  C vs A  WRΔ={ic.get('wr_delta')} PFΔ={ic.get('pf_delta')} EVΔ={ic.get('ev_delta')}",
            "",
            "====================",
            "",
            f"elapsed={result.get('elapsed_sec')}s research_only=true",
        ]
    )
    return "\n".join(lines)


def format_paper_book_md(result: dict[str, Any]) -> str:
    a = result.get("book_a") or {}
    b = result.get("book_b") or {}
    c = result.get("book_c") or {}
    ov = result.get("overlap") or {}
    lines = [
        "# PAPER_BOOK_REPORT",
        "",
        "_Market Paper Decision A/B/C V1 — research only._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- journal rows: {result.get('n_journal')}",
        "",
        "## BOOK A (paper_baseline)",
        f"- Trades: {a.get('trades')}",
        f"- WR: {a.get('wr')}",
        f"- PF: {_pf(a.get('pf'))}",
        f"- EV: {a.get('ev')}",
        f"- Sharpe: {a.get('sharpe')}",
        f"- MaxDD: {a.get('max_dd')}",
        "",
        "## BOOK B (paper_decision)",
        f"- Trades: {b.get('trades')}",
        f"- WR: {b.get('wr')}",
        f"- PF: {_pf(b.get('pf'))}",
        f"- EV: {b.get('ev')}",
        f"- Sharpe: {b.get('sharpe')}",
        f"- MaxDD: {b.get('max_dd')}",
        "",
        "## BOOK C (paper_high_confidence)",
        f"- Trades: {c.get('trades')}",
        f"- WR: {c.get('wr')}",
        f"- PF: {_pf(c.get('pf'))}",
        f"- EV: {c.get('ev')}",
        f"- Sharpe: {c.get('sharpe')}",
        f"- MaxDD: {c.get('max_dd')}",
        "",
        "## Overlap",
        f"- A∩B: {ov.get('a_and_b')}",
        f"- A∩C: {ov.get('a_and_c')}",
        f"- B∩C: {ov.get('b_and_c')}",
        f"- Filtered A−B: {ov.get('filtered_b')}",
        f"- Filtered A−C: {ov.get('filtered_c')}",
        "",
        "## Improvement",
        f"- B vs A: `{result.get('improvement_b')}`",
        f"- C vs A: `{result.get('improvement_c')}`",
        "",
    ]
    return "\n".join(lines)


def format_book_comparison_md(result: dict[str, Any]) -> str:
    return (
        "# BOOK_COMPARISON\n\n"
        + format_paper_book_md(result).replace("# PAPER_BOOK_REPORT", "## Summary")
    )


def format_decision_journal_md(rows: Sequence[dict[str, Any]], result: dict[str, Any]) -> str:
    lines = [
        "# DECISION_JOURNAL_REPORT",
        "",
        f"- rows: {len(rows)}",
        f"- accepted A/B/C: {(result.get('accepted') or {})}",
        "",
        "| trade_id | book | accepted | decision | conf | result | pnl |",
        "|---:|---|---:|---|---:|---|---:|",
    ]
    for r in list(rows)[:200]:
        lines.append(
            f"| {r.get('trade_id')} | {r.get('book')} | {r.get('accepted')} | "
            f"{r.get('decision')} | {r.get('confidence')} | {r.get('result')} | {r.get('pnl')} |"
        )
    if len(rows) > 200:
        lines.append(f"\n_… truncated, {len(rows) - 200} more rows_")
    lines.append("")
    return "\n".join(lines)


def write_book_artifacts(
    result: dict[str, Any],
    rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paper_md = format_paper_book_md(result)
    compare_md = format_book_comparison_md(result)
    journal_md = format_decision_journal_md(rows or [], result)
    paths = {
        "paper_book_report": str(BASE_DIR / "PAPER_BOOK_REPORT.md"),
        "book_comparison": str(BASE_DIR / "BOOK_COMPARISON.md"),
        "decision_journal_report": str(BASE_DIR / "DECISION_JOURNAL_REPORT.md"),
        "out_paper": str(OUT_DIR / "PAPER_BOOK_REPORT.md"),
        "out_compare": str(OUT_DIR / "BOOK_COMPARISON.md"),
        "out_journal": str(OUT_DIR / "DECISION_JOURNAL_REPORT.md"),
        "out_json": str(OUT_DIR / "paper_books.json"),
    }
    Path(paths["paper_book_report"]).write_text(paper_md, encoding="utf-8")
    Path(paths["book_comparison"]).write_text(compare_md, encoding="utf-8")
    Path(paths["decision_journal_report"]).write_text(journal_md, encoding="utf-8")
    Path(paths["out_paper"]).write_text(paper_md, encoding="utf-8")
    Path(paths["out_compare"]).write_text(compare_md, encoding="utf-8")
    Path(paths["out_journal"]).write_text(journal_md, encoding="utf-8")
    Path(paths["out_json"]).write_text(
        json.dumps({k: v for k, v in result.items() if k != "terminal"}, indent=2, default=str),
        encoding="utf-8",
    )
    return paths


__all__ = [
    "format_book_comparison_md",
    "format_book_terminal",
    "format_decision_journal_md",
    "format_paper_book_md",
    "write_book_artifacts",
]
