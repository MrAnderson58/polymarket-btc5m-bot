"""Scoped rebuild of derived thesis data for one channel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.futures_agent.db import validate_write_table


@dataclass
class RebuildThesesReport:
    channel: str
    theses_before: int = 0
    levels_before: int = 0
    outcomes_before: int = 0
    theses_deleted: int = 0
    levels_deleted: int = 0
    outcomes_deleted: int = 0
    theses_after: int = 0
    levels_after: int = 0
    posts_preserved: int = 0


def _count_channel_derived(conn: Any, channel: str) -> tuple[int, int, int, int]:
    theses = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.channel_name = ?
        """,
        (channel,),
    ).fetchone()["n"]
    levels = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_levels l
        JOIN futures_agent_trader_theses t ON t.id = l.thesis_id
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.channel_name = ?
        """,
        (channel,),
    ).fetchone()["n"]
    outcomes = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM futures_agent_thesis_outcomes o
        JOIN futures_agent_trader_theses t ON t.id = o.thesis_id
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.channel_name = ?
        """,
        (channel,),
    ).fetchone()["n"]
    posts = conn.execute(
        "SELECT COUNT(*) AS n FROM futures_agent_trader_posts WHERE channel_name = ?",
        (channel,),
    ).fetchone()["n"]
    return theses, levels, outcomes, posts


def rebuild_theses_for_channel(conn: Any, *, channel: str) -> RebuildThesesReport:
    """Delete derived theses/levels/outcomes for one channel; preserve trader_posts."""
    validate_write_table("futures_agent_trader_theses")
    report = RebuildThesesReport(channel=channel)
    tb, lb, ob, posts = _count_channel_derived(conn, channel)
    report.theses_before = tb
    report.levels_before = lb
    report.outcomes_before = ob
    report.posts_preserved = posts

    conn.execute(
        """
        DELETE FROM futures_agent_thesis_outcomes
        WHERE thesis_id IN (
            SELECT t.id
            FROM futures_agent_trader_theses t
            JOIN futures_agent_trader_posts p ON p.id = t.post_id
            WHERE p.channel_name = ?
        )
        """,
        (channel,),
    )
    report.outcomes_deleted = ob

    conn.execute(
        """
        DELETE FROM futures_agent_trader_levels
        WHERE thesis_id IN (
            SELECT t.id
            FROM futures_agent_trader_theses t
            JOIN futures_agent_trader_posts p ON p.id = t.post_id
            WHERE p.channel_name = ?
        )
        """,
        (channel,),
    )
    report.levels_deleted = lb

    conn.execute(
        """
        DELETE FROM futures_agent_trader_theses
        WHERE post_id IN (
            SELECT id FROM futures_agent_trader_posts WHERE channel_name = ?
        )
        """,
        (channel,),
    )
    report.theses_deleted = tb

    conn.commit()

    ta, la, _, posts_after = _count_channel_derived(conn, channel)
    report.theses_after = ta
    report.levels_after = la
    report.posts_preserved = posts_after
    return report


def render_rebuild_theses_report(report: RebuildThesesReport) -> str:
    lines = [
        "RESEARCH REBUILD THESES (scoped)",
        f"channel: {report.channel}",
        "",
        "BEFORE:",
        f"  theses: {report.theses_before:,}",
        f"  levels: {report.levels_before:,}",
        f"  outcomes: {report.outcomes_before:,}",
        f"  posts preserved: {report.posts_preserved:,}",
        "",
        "DELETED:",
        f"  theses: {report.theses_deleted:,}",
        f"  levels: {report.levels_deleted:,}",
        f"  outcomes: {report.outcomes_deleted:,}",
        "",
        "AFTER:",
        f"  theses: {report.theses_after:,}",
        f"  levels: {report.levels_after:,}",
        f"  posts preserved: {report.posts_preserved:,}",
        "",
        "Next: python -m bot.research.futures_agent thesis-extract "
        f"--channel {report.channel}",
    ]
    return "\n".join(lines)
