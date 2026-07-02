"""Report section builder for AI Agent."""

from __future__ import annotations

import sqlite3

from bot.ai_agent.daily import build_daily_report


def build_ai_agent_section(conn: sqlite3.Connection) -> dict:
    return build_daily_report(conn)
