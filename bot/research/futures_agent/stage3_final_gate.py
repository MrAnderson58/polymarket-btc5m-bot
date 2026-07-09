"""Stage 3 final GO/NO-GO gate before Phase D."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.missing_thesis_audit import run_missing_thesis_audit
from bot.research.futures_agent.research_reconciliation import (
    ThesisExtractStats,
    render_thesis_extract_report,
    run_pipeline_reconciliation,
)
from bot.research.futures_agent.target_contamination import run_target_contamination_audit
from bot.research.futures_agent.technical_levels_audit import run_technical_levels_audit
from bot.research.futures_agent.thesis_quality_audit import run_explicit_signal_quality_gate


@dataclass
class Stage3GateReport:
    channel: str
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    go: bool = False


def _count_channel(conn: Any, channel: str, sql: str) -> int:
    return conn.execute(sql, (channel,)).fetchone()["n"]


def run_stage3_final_gate(
    conn: Any,
    *,
    channel: str = "signalyp",
) -> Stage3GateReport:
    report = Stage3GateReport(channel=channel)

    posts = _count_channel(
        conn, channel,
        "SELECT COUNT(*) AS n FROM futures_agent_trader_posts WHERE channel_name = ?",
    )
    theses = _count_channel(
        conn, channel,
        """
        SELECT COUNT(*) AS n FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.channel_name = ?
        """,
    )
    levels = _count_channel(
        conn, channel,
        """
        SELECT COUNT(*) AS n FROM futures_agent_trader_levels l
        JOIN futures_agent_trader_theses t ON t.id = l.thesis_id
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.channel_name = ?
        """,
    )
    outcomes = _count_channel(
        conn, channel,
        """
        SELECT COUNT(*) AS n FROM futures_agent_thesis_outcomes o
        JOIN futures_agent_trader_theses t ON t.id = o.thesis_id
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.channel_name = ?
        """,
    )

    report.sections["SIGNALYP CORPUS"] = (
        f"posts: {posts:,}\n"
        f"theses: {theses:,}\n"
        f"levels: {levels:,}\n"
        f"outcomes: {outcomes:,}"
    )

    # Verify report rendering does not crash
    try:
        rendered = render_thesis_extract_report(
            conn, ThesisExtractStats(), channel=channel,
        )
        report.sections["THESIS EXTRACTION"] = (
            "render_thesis_extract_report: OK\n"
            + "\n".join(rendered.splitlines()[:12])
        )
    except Exception as exc:
        report.blockers.append(f"render_thesis_extract_report crashed: {exc}")
        report.sections["THESIS EXTRACTION"] = f"FAIL: {exc}"

    pipeline = run_pipeline_reconciliation(conn, channel=channel)
    report.sections["FK / DUPLICATE INTEGRITY"] = (
        f"extraction_status: {pipeline.extraction_status}\n"
        f"eligible_posts: {pipeline.eligible_posts:,}\n"
        f"posts_with_theses: {pipeline.posts_with_theses:,}\n"
        f"eligible_without_theses: {pipeline.eligible_posts_without_theses:,}"
    )
    for name, ok, detail in pipeline.checks:
        if not ok:
            report.blockers.append(f"{name}: {detail}")

    contam = run_target_contamination_audit(conn, channel=channel)
    gate = run_explicit_signal_quality_gate(conn, channel=channel)
    report.sections["EXPLICIT_SIGNAL QUALITY"] = (
        f"total: {gate.total_explicit_theses:,}\n"
        f"numeric entry: {gate.numeric_entry:,}\n"
        f"market entry: {gate.market_entry:,}\n"
        f"with targets: {gate.with_targets:,}"
    )
    report.sections["TARGET CONTAMINATION"] = (
        f"contaminated: {len(contam.contaminated_rows):,} / {contam.total_explicit_theses:,}"
    )
    if gate.url_number_contamination:
        report.blockers.append(f"URL-number contamination: {gate.url_number_contamination}")
    if gate.percentage_contamination:
        report.blockers.append(f"percentage contamination: {gate.percentage_contamination}")
    if gate.suspicious_target_contamination:
        report.blockers.append(
            f"suspicious target contamination: {gate.suspicious_target_contamination}",
        )
    if contam.contaminated_rows:
        report.blockers.append(
            f"target contamination audit rows: {len(contam.contaminated_rows)}",
        )

    tech = run_technical_levels_audit(conn, channel=channel)
    report.sections["TECHNICAL_LEVELS QUALITY"] = (
        f"posts: {tech.total_posts:,} theses: {tech.theses_created:,}\n"
        f"missing: {tech.missing_theses:,}\n"
        f"support: {tech.support_count:,} resistance: {tech.resistance_count:,}\n"
        f"suspicious levels: {len(tech.suspicious_levels):,}"
    )
    if tech.suspicious_levels:
        fib_like = sum(
            1 for s in tech.suspicious_levels
            if "likely_fibonacci_ratio_not_price" in s.reasons
        )
        report.warnings.append(
            f"TECHNICAL_LEVELS suspicious levels: {len(tech.suspicious_levels)} "
            f"(fib-like: {fib_like}) — isolate from EXPLICIT_SIGNAL evaluation",
        )

    missing = run_missing_thesis_audit(conn, channel=channel)
    report.sections["MISSING THESES"] = (
        f"missing: {missing.missing_count:,}\n"
        f"by_reason: {missing.by_reason}"
    )
    if missing.missing_count > 0:
        tech_missing = missing.by_content_type.get("TECHNICAL_LEVELS", 0)
        if tech_missing == missing.missing_count:
            report.warnings.append(
                f"All {missing.missing_count} missing theses are TECHNICAL_LEVELS "
                f"(expected unresolved without symbol/evidence)",
            )
        else:
            report.blockers.append(
                f"non-technical missing theses: "
                f"{missing.missing_count - tech_missing}",
            )

    report.sections["IDEMPOTENCY"] = (
        "Re-run thesis-extract --channel signalyp; expect posts_scanned=0 "
        "and theses_inserted=0 if extraction committed before prior report crash."
    )

    report.sections["TEST STATUS"] = (
        "Run pytest locally before gate:\n"
        "  tests/test_futures_agent_stage3.py tests/test_futures_agent_migrations.py\n"
        "  tests/test_futures_agent_stage1.py tests/test_futures_agent_stage2.py"
    )

    report.go = len(report.blockers) == 0
    return report


def render_stage3_final_gate(report: Stage3GateReport) -> str:
    lines = [
        "STAGE 3 FINAL GATE",
        f"channel: {report.channel}",
        "",
    ]
    order = (
        "TEST STATUS",
        "SIGNALYP CORPUS",
        "THESIS EXTRACTION",
        "EXPLICIT_SIGNAL QUALITY",
        "TARGET CONTAMINATION",
        "TECHNICAL_LEVELS QUALITY",
        "MISSING THESES",
        "IDEMPOTENCY",
        "FK / DUPLICATE INTEGRITY",
        "PHASE D READINESS",
    )
    for key in order:
        if key in report.sections:
            lines.append(f"=== {key} ===")
            lines.append(report.sections[key])
            lines.append("")

    if report.warnings:
        lines.append("WARNINGS:")
        for w in report.warnings:
            lines.append(f"  - {w}")
        lines.append("")

    if report.blockers:
        lines.append("BLOCKERS:")
        for b in report.blockers:
            lines.append(f"  - {b}")
        lines.append("")
        lines.append("RECOMMENDATION: NO-GO FOR PHASE D")
    else:
        lines.append("RECOMMENDATION: GO FOR PHASE D")
        if report.warnings:
            lines.append("(with TECHNICAL_LEVELS warnings noted above)")

    return "\n".join(lines)
