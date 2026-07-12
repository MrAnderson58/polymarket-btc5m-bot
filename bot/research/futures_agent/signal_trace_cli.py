"""Phase F.5.1 — signal trace CLI (reads market_events DB)."""

from __future__ import annotations

from typing import Any


def run_signal_trace(*, last: int = 20) -> str:
    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.event_schema import apply_migrations
    from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
        format_signal_trace,
        recent_traced_event_ids,
    )

    blocks: list[str] = []
    with market_events_connection() as conn:
        apply_migrations(conn)
        event_ids = recent_traced_event_ids(conn, limit=last)
        if not event_ids:
            return "No signal traces recorded yet.\n"
        for event_id in event_ids:
            blocks.append(format_signal_trace(conn, event_id=event_id))
            blocks.append("-" * 48)
    return "\n".join(blocks).rstrip() + "\n"
