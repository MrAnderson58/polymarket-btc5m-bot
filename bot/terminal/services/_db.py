"""Shared read helpers for Terminal services (existing DBs only)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


@contextmanager
def market_events_ro() -> Iterator[Any]:
    """Open existing market_events DB read-only. Raises if unavailable."""
    from bot.research.market_events.db import market_events_readonly_connection

    with market_events_readonly_connection() as conn:
        yield conn
