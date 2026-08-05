"""Markdown reports for Paper Mathematics Validation V1."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "paper_math_validation_v1"


def _fmt_book(name: str, m: dict[str, Any]) -> list[str]:
    return [
        f"### {name}",
        f"- trades: {m.get('trades')}",
        f"- accepted: {m.get('accepted')}",
        f"- rejected: {m.get('rejected')}",
        f"- WR: {m.get('wr')}",
        f"- PF: {m.get('pf')}",
        f"- EV: {m.get('ev')}",
        f"- Sharpe: {m.get('sharpe')}",
        f"- MaxDD: {m.get('max_dd')}",
        f"- avg confidence: {m.get('avg_confidence')}",
        f"- avg reality: {m.get('avg_reality')}",
        f"- expected WR: {m.get('expected_wr')}",
        f"- expected EV: {m.get('expected_ev')}",
        "",
    ]


def write_reports(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cons = result.get("consistency") or {}

    paper = "\n".join([
        "# PAPER_MATH_REPORT",
        "",
        "_Paper Mathematics Validation V1 — research / paper only. No execution._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- candidates: **{result.get('n_candidates')}**",
        f"- reality_consistency: **{result.get('reality_consistency')}**",
        f"- elite_consistency: **{result.get('elite_consistency')}**",
        f"- dataset_version: `{cons.get('dataset_version')}`",
        "",
        "## Book D (reference)",
        *_fmt_book("Strict Mathematics", result.get("book_d") or {}),
        "## Book C",
        *_fmt_book("Mathematical Elite", result.get("book_c") or {}),
    ])

    daily = "\n".join([
        "# PAPER_MATH_DAILY",
        "",
        "## STRICT MATHEMATICS",
        f"- Candidates today (universe): {result.get('n_candidates')}",
        f"- Accepted (Book D): {(result.get('book_d') or {}).get('accepted')}",
        f"- Rejected (Book D): {(result.get('book_d') or {}).get('rejected')}",
        f"- Average confidence: {(result.get('book_d') or {}).get('avg_confidence')}",
        f"- Average Reality: {(result.get('book_d') or {}).get('avg_reality')}",
        f"- Expected WR: {(result.get('book_d') or {}).get('expected_wr')}",
        f"- Actual WR: {(result.get('book_d') or {}).get('wr')}",
        f"- Expected EV: {(result.get('book_d') or {}).get('expected_ev')}",
        f"- Actual EV: {(result.get('book_d') or {}).get('ev')}",
        f"- PF: {(result.get('book_d') or {}).get('pf')}",
        f"- Sharpe: {(result.get('book_d') or {}).get('sharpe')}",
        f"- Drawdown: {(result.get('book_d') or {}).get('max_dd')}",
        "",
        "## Top rejection reasons",
        *[f"- {k}: {v}" for k, v in (result.get("top_rejections") or [])[:12]],
        "",
        "## Top profitable reasons",
        *[f"- {k}: {v}" for k, v in (result.get("top_profitable_reasons") or [])[:12]],
        "",
    ])

    compare = "\n".join([
        "# BOOK_COMPARISON",
        "",
        "_Paper Math Validation — A/B/C/D daily reality check._",
        "",
        "| Book | Trades | WR | PF | EV | Sharpe |",
        "|------|--------|----|----|----|--------|",
        *[
            (
                f"| {label} | {(result.get(key) or {}).get('trades')} | "
                f"{(result.get(key) or {}).get('wr')} | {(result.get(key) or {}).get('pf')} | "
                f"{(result.get(key) or {}).get('ev')} | {(result.get(key) or {}).get('sharpe')} |"
            )
            for label, key in (
                ("A baseline", "book_a"),
                ("B decision", "book_b"),
                ("C math elite", "book_c"),
                ("D strict math", "book_d"),
            )
        ],
        "",
        "## Consistency",
        f"- Reality: {cons.get('reality')}",
        f"- Elite: {cons.get('elite')}",
        f"- Decision alignment: {cons.get('decision')}",
        f"- Morning: {cons.get('morning')}",
        f"- Book D duplicates: {cons.get('book_d_duplicates')}",
        "",
    ])

    entry_q = "\n".join([
        "# ENTRY_QUALITY",
        "",
        "_Only mathematically validated TRADE entries. No AI voting. No experiments._",
        "",
        f"- learning_events: {result.get('learning_events_n')}",
        f"- exit_results: {result.get('exit_results_n')}",
        "",
        "## Learning sample (no strategy change)",
        *[
            f"- {e.get('type')}: trade={e.get('trade_id')} ({e.get('detail')})"
            for e in (result.get("learning_sample") or [])[:20]
        ],
        "",
        "## Entry gates",
        "- Decision=TRADE, Rank>=A, Confidence>=75%, Brain supports,",
        "- Reality>=80, Hist WR>=70%, PF>=1.80, EV>0,",
        "- Timeline>=60%, Fingerprint>=30%, Replay/DNA/Rules MATCH,",
        "- Not REGIME_EXPLORE, market not UNKNOWN, no Book duplicate.",
        "",
    ])

    files = {
        "PAPER_MATH_REPORT.md": paper,
        "PAPER_MATH_DAILY.md": daily,
        "BOOK_COMPARISON.md": compare,
        "ENTRY_QUALITY.md": entry_q,
    }
    paths: dict[str, str] = {}
    for name, text in files.items():
        (BASE_DIR / name).write_text(text, encoding="utf-8")
        (OUT_DIR / name).write_text(text, encoding="utf-8")
        paths[name] = str(BASE_DIR / name)

    slim = {k: result.get(k) for k in (
        "ok", "elapsed_sec", "n_candidates", "book_a", "book_b", "book_c", "book_d",
        "reality_consistency", "elite_consistency", "consistency",
        "top_rejections", "top_profitable_reasons",
    )}
    jp = OUT_DIR / "paper_math.json"
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


__all__ = ["write_reports"]
