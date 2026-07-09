"""Audit eligible posts that have no extracted thesis."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.research_reconciliation import THESIS_ELIGIBLE_TYPES
from bot.research.futures_agent.research_utils import (
    extract_research_symbols,
    normalize_json_array,
)
from bot.research.futures_agent.thesis_extract import extract_theses_from_post


@dataclass
class MissingThesisRow:
    post_id: int
    content_type: str
    channel_name: str
    raw_preview: str
    symbols: list[str]
    reason: str


@dataclass
class MissingThesisAuditReport:
    channel: str | None
    eligible_posts: int = 0
    posts_with_theses: int = 0
    missing_count: int = 0
    rows: list[MissingThesisRow] = field(default_factory=list)
    by_content_type: dict[str, int] = field(default_factory=dict)
    by_reason: dict[str, int] = field(default_factory=dict)


def _classify_missing_reason(
    raw_text: str,
    content_type: str,
    symbols: list[str],
) -> str:
    theses = extract_theses_from_post(raw_text, content_type, symbols=symbols)
    if not theses:
        return "extract_returned_empty"
    if all(t.unresolved for t in theses):
        if not symbols:
            return "no_symbol_low_confidence"
        if all(t.direction == "NEUTRAL" for t in theses):
            return "neutral_direction_unresolved"
        return "unresolved_low_confidence"
    resolved = [t for t in theses if not t.unresolved]
    if not resolved:
        return "all_theses_unresolved"
    return "unexpected_missing"


def run_missing_thesis_audit(
    conn: Any,
    *,
    channel: str | None = "signalyp",
) -> MissingThesisAuditReport:
    report = MissingThesisAuditReport(channel=channel)
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        params.append(channel)

    eligible_ph = ",".join("?" for _ in THESIS_ELIGIBLE_TYPES)
    report.eligible_posts = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM futures_agent_trader_posts p
        WHERE p.content_type IN ({eligible_ph}){ch_clause}
        """,
        [*THESIS_ELIGIBLE_TYPES, *params],
    ).fetchone()["n"]
    report.posts_with_theses = conn.execute(
        f"""
        SELECT COUNT(DISTINCT p.id) AS n
        FROM futures_agent_trader_posts p
        JOIN futures_agent_trader_theses t ON t.post_id = p.id
        WHERE p.content_type IN ({eligible_ph}){ch_clause}
        """,
        [*THESIS_ELIGIBLE_TYPES, *params],
    ).fetchone()["n"]

    rows = conn.execute(
        f"""
        SELECT p.id, p.channel_name, p.content_type, p.raw_text, p.symbols_json
        FROM futures_agent_trader_posts p
        LEFT JOIN futures_agent_trader_theses t ON t.post_id = p.id
        WHERE t.id IS NULL
          AND p.content_type IN ({eligible_ph}){ch_clause}
        ORDER BY p.content_type, p.id
        """,
        [*THESIS_ELIGIBLE_TYPES, *params],
    ).fetchall()

    report.missing_count = len(rows)
    type_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()

    for row in rows:
        symbols = [str(s) for s in normalize_json_array(row["symbols_json"])]
        if not symbols:
            symbols = extract_research_symbols(row["raw_text"] or "")
        reason = _classify_missing_reason(
            row["raw_text"], row["content_type"], symbols,
        )
        type_counter[row["content_type"]] += 1
        reason_counter[reason] += 1
        report.rows.append(MissingThesisRow(
            post_id=row["id"],
            content_type=row["content_type"],
            channel_name=row["channel_name"],
            raw_preview=row["raw_text"][:300].replace("\n", " "),
            symbols=symbols,
            reason=reason,
        ))

    report.by_content_type = dict(type_counter)
    report.by_reason = dict(reason_counter)
    return report


def render_missing_thesis_audit(report: MissingThesisAuditReport) -> str:
    lines = [
        "MISSING THESIS AUDIT (eligible posts without theses)",
        f"channel: {report.channel or 'all'}",
        f"eligible_posts: {report.eligible_posts:,}",
        f"posts_with_theses: {report.posts_with_theses:,}",
        f"missing: {report.missing_count:,}",
        "",
        "By content_type:",
    ]
    for ctype, n in sorted(report.by_content_type.items()):
        lines.append(f"  {ctype}: {n}")
    lines.append("")
    lines.append("By reason:")
    for reason, n in sorted(report.by_reason.items()):
        lines.append(f"  {reason}: {n}")
    lines.append("")
    if report.missing_count == 0:
        lines.append("No eligible posts missing theses.")
        return "\n".join(lines)

    lines.append("Posts:")
    for row in report.rows:
        lines.append(
            f"  post={row.post_id} [{row.content_type}] "
            f"symbols={row.symbols} reason={row.reason}",
        )
        lines.append(f"    preview: {row.raw_preview}")
    lines.append("")
    if report.missing_count > 0 and report.missing_count <= 50:
        lines.append(
            "Assessment: small missing count is expected when posts lack "
            "symbol/direction/evidence (unresolved skips are intentional).",
        )
    return "\n".join(lines)
