"""Futures signal intelligence research report and verdict."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.futures.config import MIN_SOURCE_N, MIN_WALK_FORWARD_TRADES
from bot.research.futures.models_abc import walk_forward_abc
from bot.research.futures.patterns import discover_patterns
from bot.research.futures.schema import PARSE_AUDIT_TABLE, SIGNALS_TABLE, ensure_tables
from bot.research.futures.source_audit import audit_source_data, render_audit_report
from bot.research.futures.source_scoring import score_sources


VERDICTS = (
    "NO_EDGE",
    "COLLECT_MORE_DATA",
    "SIGNALS_HAVE_EDGE",
    "MARKET_MODEL_HAS_EDGE",
    "SIGNAL_PLUS_MARKET_HAS_EDGE",
    "READY_FOR_FUTURES_PAPER_SHADOW",
)


def determine_verdict(
    audit: dict[str, Any],
    wf: dict[str, Any],
    leaderboard: list[dict[str, Any]],
) -> str:
    msg_count = audit["messages"]["count"]
    parsed = audit.get("research_signals_parsed", 0)

    if msg_count < 50 and parsed < MIN_WALK_FORWARD_TRADES:
        return "COLLECT_MORE_DATA"

    strong_sources = [s for s in leaderboard if s.get("sufficient_sample") and s.get("pf", 0) >= 1.3]
    if strong_sources:
        signal_edge = True
    else:
        signal_edge = False

    models = wf.get("models", {})
    b_pf = models.get("MODEL_B_MARKET_ONLY", {}).get("pf", 0)
    c_pf = models.get("MODEL_C_SIGNAL_PLUS_MARKET", {}).get("pf", 0)
    a_pf = models.get("MODEL_A_SIGNAL_ONLY", {}).get("pf", 0)

    if wf.get("status") == "insufficient_data":
        return "COLLECT_MORE_DATA" if msg_count > 0 else "NO_EDGE"

    if c_pf > 1.2 and wf.get("c_beats_b"):
        if strong_sources and parsed >= MIN_WALK_FORWARD_TRADES:
            return "READY_FOR_FUTURES_PAPER_SHADOW"
        return "SIGNAL_PLUS_MARKET_HAS_EDGE"
    if b_pf > 1.2:
        return "MARKET_MODEL_HAS_EDGE"
    if signal_edge or a_pf > 1.2:
        return "SIGNALS_HAVE_EDGE"
    if msg_count == 0:
        return "NO_EDGE"
    return "COLLECT_MORE_DATA"


def build_report(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_tables(conn)
    audit = audit_source_data(conn)
    wf = walk_forward_abc(conn)
    leaderboard = score_sources(conn)
    patterns = discover_patterns(conn)

    parse_stats = conn.execute(
        f"""
        SELECT parse_status, COUNT(*) AS n
        FROM {PARSE_AUDIT_TABLE}
        GROUP BY parse_status
        """
    ).fetchall()
    signal_count = conn.execute(f"SELECT COUNT(*) FROM {SIGNALS_TABLE}").fetchone()[0]
    verdict = determine_verdict(audit, wf, leaderboard)

    return {
        "audit": audit,
        "parser_quality": {r["parse_status"]: r["n"] for r in parse_stats},
        "signal_count": signal_count,
        "source_leaderboard": leaderboard[:20],
        "patterns": patterns,
        "walk_forward": wf,
        "verdict": verdict,
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        render_audit_report(report["audit"]),
        "",
        "PARSER QUALITY",
    ]
    for k, v in report.get("parser_quality", {}).items():
        lines.append(f"  {k}: {v}")
    lines.extend([
        "",
        f"Parsed signals: {report['signal_count']}",
        "",
        "SOURCE LEADERBOARD (top)",
    ])
    for s in report.get("source_leaderboard", [])[:10]:
        flag = " *" if s.get("sufficient_sample") else " (low N)"
        lines.append(
            f"  {s['source']}{flag}: N={s['n']} PF={s['pf']:.2f} "
            f"acc={s['directional_accuracy']:.1%} avg_pnl={s['avg_pnl']:.2f}"
        )

    lines.extend(["", "WALK-FORWARD A/B/C"])
    wf = report.get("walk_forward", {})
    if wf.get("status") == "insufficient_data":
        lines.append(f"  insufficient data: n={wf.get('n')} need {wf.get('min_required')}")
    else:
        for name, metrics in wf.get("models", {}).items():
            lines.append(
                f"  {name}: PF={metrics.get('pf', 0):.2f} "
                f"prec={metrics.get('precision', 0):.2f} "
                f"AUC={metrics.get('roc_auc')}"
            )
        if "primary_question" in wf:
            lines.append(f"  => {wf['primary_question']}")

    if report.get("patterns"):
        lines.extend(["", "CONFIRMED PATTERNS (OOS)"])
        for p in report["patterns"]:
            if p.get("oos_confirmed"):
                lines.append(
                    f"  {p['label']}: train_wr={p['train_wr']:.1%} oos_wr={p['oos_wr']:.1%} "
                    f"oos_n={p['oos_n']}"
                )

    lines.extend(["", f"VERDICT: {report['verdict']}"])
    return "\n".join(lines)
