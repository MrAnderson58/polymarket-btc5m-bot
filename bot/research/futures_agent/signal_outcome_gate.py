"""Phase D.1 final gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.signal_outcome_build import OutcomeBuildStats, build_signal_outcomes, render_build_reconciliation
from bot.research.futures_agent.signal_outcome_constants import ENGINE_VERSION
from bot.research.futures_agent.signal_outcome_report import render_outcome_report, run_outcome_report


@dataclass
class OutcomeGateReport:
    channel: str
    engine_version: str
    blockers: list[str] = field(default_factory=list)
    build_stats: OutcomeBuildStats | None = None
    report_text: str = ""


def run_outcome_gate(
    conn: Any,
    *,
    channel: str = "signalyp",
    engine_version: str = ENGINE_VERSION,
) -> OutcomeGateReport:
    gate = OutcomeGateReport(channel=channel, engine_version=engine_version)

    expected = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.channel_name = ? AND p.content_type = 'EXPLICIT_SIGNAL'
        """,
        (channel,),
    ).fetchone()["n"]

    built = conn.execute(
        """
        SELECT COUNT(*) AS n FROM futures_agent_research_signal_outcomes
        WHERE channel = ? AND engine_version = ?
        """,
        (channel, engine_version),
    ).fetchone()["n"]

    dup = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
          SELECT thesis_id, engine_version, COUNT(*) AS c
          FROM futures_agent_research_signal_outcomes
          WHERE channel = ?
          GROUP BY thesis_id, engine_version
          HAVING COUNT(*) > 1
        ) x
        """,
        (channel,),
    ).fetchone()["n"]
    if dup:
        gate.blockers.append(f"duplicate outcomes: {dup}")

    orphan_events = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM futures_agent_research_signal_events e
        LEFT JOIN futures_agent_research_signal_outcomes o ON o.id = e.outcome_id
        WHERE o.id IS NULL
        """,
    ).fetchone()["n"]
    if orphan_events:
        gate.blockers.append(f"orphan events: {orphan_events}")

    if built < expected:
        gate.blockers.append(f"incomplete build: {built}/{expected} outcomes")

    report = run_outcome_report(conn, channel=channel, engine_version=engine_version)
    gate.report_text = render_outcome_report(report)
    if report.verdict == "INSUFFICIENT_DATA":
        gate.blockers.append(f"verdict: {report.verdict}")

    return gate


def render_outcome_gate(gate: OutcomeGateReport) -> str:
    lines = [
        "PHASE D.1 FINAL GATE",
        f"channel: {gate.channel}",
        f"engine_version: {gate.engine_version}",
        "",
    ]
    if gate.build_stats:
        lines.append(render_build_reconciliation(gate.build_stats))
        lines.append("")
    if gate.blockers:
        lines.append("BLOCKERS:")
        for b in gate.blockers:
            lines.append(f"  - {b}")
        lines.append("")
        lines.append("STATUS: NO-GO")
    else:
        lines.append("STATUS: GO (research outcome engine ready)")
    lines.append("")
    lines.append(gate.report_text)
    return "\n".join(lines)
