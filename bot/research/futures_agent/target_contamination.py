"""Target contamination diagnosis for EXPLICIT_SIGNAL theses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.signal_level_extract import (
    extract_url_number_blacklist,
    is_contaminated_target,
)

_ALLOCATION_PCTS = frozenset({25.0, 50.0, 75.0, 100.0})


@dataclass
class ContaminatedThesisRow:
    thesis_id: int
    post_id: int
    raw_preview: str
    targets: list[float]
    entry: float | None
    reasons: list[str] = field(default_factory=list)


@dataclass
class TargetContaminationAuditReport:
    channel: str | None
    total_explicit_theses: int = 0
    contaminated_rows: list[ContaminatedThesisRow] = field(default_factory=list)


def diagnose_target_contamination(
    raw_text: str,
    targets: list[float],
    *,
    entry: float | None = None,
) -> list[str]:
    """Return contamination reason codes for stored targets."""
    if not targets:
        return []
    reasons: list[str] = []
    url_blacklist = extract_url_number_blacklist(raw_text)
    for tp in targets:
        reason = is_contaminated_target(
            tp,
            entry=entry,
            url_blacklist=url_blacklist,
            raw_text=raw_text,
        )
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def run_target_contamination_audit(
    conn: Any,
    *,
    channel: str | None = "signalyp",
) -> TargetContaminationAuditReport:
    report = TargetContaminationAuditReport(channel=channel)
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        params.append(channel)

    rows = conn.execute(
        f"""
        SELECT t.id AS thesis_id, p.id AS post_id, p.raw_text
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.content_type = 'EXPLICIT_SIGNAL'{ch_clause}
        ORDER BY t.id ASC
        """,
        params,
    ).fetchall()
    report.total_explicit_theses = len(rows)

    for row in rows:
        levels = conn.execute(
            """
            SELECT level_type, price FROM futures_agent_trader_levels
            WHERE thesis_id = ? AND level_type = 'TARGET'
            ORDER BY ordinal
            """,
            (row["thesis_id"],),
        ).fetchall()
        targets = [float(r["price"]) for r in levels]
        entry_row = conn.execute(
            """
            SELECT level_type, price FROM futures_agent_trader_levels
            WHERE thesis_id = ? AND level_type IN ('ENTRY_LOW', 'ENTRY_HIGH')
            ORDER BY level_type
            """,
            (row["thesis_id"],),
        ).fetchall()
        entry = float(entry_row[0]["price"]) if entry_row else None
        reasons = diagnose_target_contamination(row["raw_text"], targets, entry=entry)
        if reasons:
            report.contaminated_rows.append(ContaminatedThesisRow(
                thesis_id=row["thesis_id"],
                post_id=row["post_id"],
                raw_preview=row["raw_text"][:400].replace("\n", " "),
                targets=targets,
                entry=entry,
                reasons=reasons,
            ))
    return report


def render_target_contamination_audit(report: TargetContaminationAuditReport) -> str:
    lines = [
        "EXPLICIT_SIGNAL TARGET CONTAMINATION AUDIT",
        f"channel: {report.channel or 'all'}",
        f"total explicit theses: {report.total_explicit_theses:,}",
        f"contaminated theses: {len(report.contaminated_rows):,}",
        "",
    ]
    if not report.contaminated_rows:
        lines.append("No contaminated targets detected.")
        return "\n".join(lines)

    for row in report.contaminated_rows:
        lines.append(f"--- thesis={row.thesis_id} post={row.post_id} ---")
        lines.append(f"  preview: {row.raw_preview}")
        lines.append(f"  entry: {row.entry}")
        lines.append(f"  targets: {row.targets}")
        lines.append(f"  contamination: {', '.join(row.reasons)}")
        lines.append("")
    return "\n".join(lines)
