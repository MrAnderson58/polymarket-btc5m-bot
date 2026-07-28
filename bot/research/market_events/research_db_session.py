"""Shared helpers for research CLI write sessions (WAL + lock-safe close)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from bot.research.market_events.db import (
    ensure_wal_enabled,
    market_events_connection,
    market_events_readonly_connection,
)


@contextmanager
def research_write_connection(db_path: Path) -> Iterator[Any]:
    """
    Writable analytics connection for research pipeline commands.
    Ensures WAL, commits on success, always closes (releases lock).
    """
    try:
        ensure_wal_enabled(db_path)
    except Exception:
        # Non-fatal: connect path still applies journal_mode=WAL pragma.
        pass
    with market_events_connection(db_path=db_path) as conn:
        yield conn


@contextmanager
def research_readonly_connection(db_path: Path) -> Iterator[Any]:
    with market_events_readonly_connection(db_path=db_path) as conn:
        yield conn
