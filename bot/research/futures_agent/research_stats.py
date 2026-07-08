"""Research corpus statistics."""

from __future__ import annotations

from typing import Any


def render_research_stats(conn: Any, *, channel: str | None = None) -> str:
    lines = ["STAGE 3 RESEARCH STATS", ""]
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " WHERE channel_name = ?"
        params = [channel]
        lines.append(f"Channel filter: {channel}")
        lines.append("")

    posts = conn.execute(
        f"SELECT COUNT(*) AS n FROM futures_agent_trader_posts{ch_clause}",
        params,
    ).fetchone()["n"]
    if channel:
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
    else:
        theses = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_trader_theses").fetchone()["n"]
        levels = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_trader_levels").fetchone()["n"]
    lines.append(f"Posts ingested: {posts}")
    lines.append(f"Theses extracted: {theses}")
    lines.append(f"Levels stored: {levels}")
    lines.append("")

    lines.append("By content_type:")
    if channel:
        ct_rows = conn.execute(
            """
            SELECT content_type, COUNT(*) AS n
            FROM futures_agent_trader_posts
            WHERE channel_name = ?
            GROUP BY content_type
            ORDER BY n DESC
            """,
            (channel,),
        ).fetchall()
    else:
        ct_rows = conn.execute(
            """
            SELECT content_type, COUNT(*) AS n
            FROM futures_agent_trader_posts
            GROUP BY content_type
            ORDER BY n DESC
            """,
        ).fetchall()
    for row in ct_rows:
        lines.append(f"  {row['content_type']}: {row['n']}")

    if not channel:
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
    if channel:
        dir_rows = conn.execute(
            """
            SELECT t.direction, COUNT(*) AS n
            FROM futures_agent_trader_theses t
            JOIN futures_agent_trader_posts p ON p.id = t.post_id
            WHERE p.channel_name = ?
            GROUP BY t.direction
            ORDER BY n DESC
            """,
            (channel,),
        ).fetchall()
    else:
        dir_rows = conn.execute(
            """
            SELECT direction, COUNT(*) AS n
            FROM futures_agent_trader_theses
            GROUP BY direction
            ORDER BY n DESC
            """,
        ).fetchall()
    for row in dir_rows:
        lines.append(f"  {row['direction']}: {row['n']}")

    lines.append("")
    lines.append("Posts without theses:")
    if channel:
        missing = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM futures_agent_trader_posts p
            LEFT JOIN futures_agent_trader_theses t ON t.post_id = p.id
            WHERE t.id IS NULL AND p.channel_name = ?
            """,
            (channel,),
        ).fetchone()["n"]
    else:
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
