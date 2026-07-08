"""Research corpus statistics."""

from __future__ import annotations

from typing import Any


def render_research_stats(conn: Any) -> str:
    lines = ["STAGE 3 RESEARCH STATS", ""]
    posts = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_trader_posts").fetchone()["n"]
    theses = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_trader_theses").fetchone()["n"]
    levels = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_trader_levels").fetchone()["n"]
    lines.append(f"Posts ingested: {posts}")
    lines.append(f"Theses extracted: {theses}")
    lines.append(f"Levels stored: {levels}")
    lines.append("")

    lines.append("By content_type:")
    for row in conn.execute(
        """
        SELECT content_type, COUNT(*) AS n
        FROM futures_agent_trader_posts
        GROUP BY content_type
        ORDER BY n DESC
        """,
    ).fetchall():
        lines.append(f"  {row['content_type']}: {row['n']}")

    lines.append("")
    lines.append("By channel:")
    for row in conn.execute(
        """
        SELECT channel_name, COUNT(*) AS n
        FROM futures_agent_trader_posts
        GROUP BY channel_name
        ORDER BY n DESC
        """,
    ).fetchall():
        lines.append(f"  {row['channel_name']}: {row['n']}")

    lines.append("")
    lines.append("Theses by direction:")
    for row in conn.execute(
        """
        SELECT direction, COUNT(*) AS n
        FROM futures_agent_trader_theses
        GROUP BY direction
        ORDER BY n DESC
        """,
    ).fetchall():
        lines.append(f"  {row['direction']}: {row['n']}")

    lines.append("")
    lines.append("Posts without theses:")
    missing = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_posts p
        LEFT JOIN futures_agent_trader_theses t ON t.post_id = p.id
        WHERE t.id IS NULL
        """,
    ).fetchone()["n"]
    lines.append(f"  {missing}")

    return "\n".join(lines)
