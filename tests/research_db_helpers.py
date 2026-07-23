"""Shared helpers for research-DB unit tests (S60)."""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    apply_research_migrations,
    research_connection,
)


def ensure_research_schema() -> None:
    with research_connection() as conn:
        apply_research_migrations(conn)
        try:
            conn.commit()
        except Exception:
            pass
