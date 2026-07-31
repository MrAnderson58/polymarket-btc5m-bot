"""Shared helpers for research CLI write sessions (WAL + serialized writer).

Research Concurrency Fix V1:
- Cross-process exclusive flock so only one research writer touches analytics SQLite
- In-process RLock held for the full write critical section (no parallel writers)
- Nested same-thread sessions share one flock via thread-local depth
- WAL + busy_timeout + retry remain on the connection path
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import (
    ensure_wal_enabled,
    market_events_connection,
    market_events_readonly_connection,
)

RESEARCH_WRITE_LOCK_PATH = BASE_DIR / "data" / "market_events_research_write.lock"

# Serialize all research writers inside this process for the whole critical section.
_PROCESS_MUTEX = threading.RLock()
_LOCAL = threading.local()

# Extra guard for knowledge_history inserts (also covered by write lock when used via CLI).
KNOWLEDGE_HISTORY_LOCK = threading.RLock()


def _acquire_flock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _release_flock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def research_write_lock() -> Iterator[None]:
    """
    Exclusive research writer lock (process-local RLock + cross-process flock).

    Nested calls in the same thread share one flock (thread-local depth).
    Other threads block on the RLock until the critical section exits.
    """
    with _PROCESS_MUTEX:
        depth = int(getattr(_LOCAL, "depth", 0) or 0)
        if depth == 0:
            RESEARCH_WRITE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(RESEARCH_WRITE_LOCK_PATH), os.O_CREAT | os.O_RDWR)
            try:
                _acquire_flock(fd)
            except Exception:
                os.close(fd)
                raise
            _LOCAL.fd = fd
        _LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _LOCAL.depth = int(getattr(_LOCAL, "depth", 1) or 1) - 1
            if _LOCAL.depth <= 0:
                _LOCAL.depth = 0
                fd = getattr(_LOCAL, "fd", None)
                _LOCAL.fd = None
                if fd is not None:
                    try:
                        _release_flock(fd)
                    finally:
                        os.close(fd)


@contextmanager
def knowledge_history_write_guard() -> Iterator[None]:
    """In-process serialize knowledge_history inserts (no parallel history writers)."""
    with KNOWLEDGE_HISTORY_LOCK:
        yield


@contextmanager
def research_write_connection(db_path: Path) -> Iterator[Any]:
    """
    Writable analytics connection for research pipeline commands.

    Guarantees: WAL best-effort, exclusive research write lock, commit on success,
    always close (releases SQLite lease).
    """
    try:
        ensure_wal_enabled(db_path)
    except Exception:
        # Non-fatal: connect path still applies journal_mode=WAL pragma.
        pass
    with research_write_lock():
        with market_events_connection(db_path=db_path) as conn:
            yield conn


@contextmanager
def research_readonly_connection(db_path: Path) -> Iterator[Any]:
    with market_events_readonly_connection(db_path=db_path) as conn:
        yield conn


@contextmanager
def research_migrate_then_readonly(db_path: Path) -> Iterator[Any]:
    """
    Short locked migrate, then yield a readonly connection for long compute.

    Prevents alpha-engine / similar jobs from holding a write lease during mining.
    """
    from bot.research.market_events.event_schema import apply_migrations

    with research_write_connection(db_path) as wconn:
        apply_migrations(wconn)
    with research_readonly_connection(db_path) as rconn:
        yield rconn


__all__ = [
    "KNOWLEDGE_HISTORY_LOCK",
    "RESEARCH_WRITE_LOCK_PATH",
    "knowledge_history_write_guard",
    "research_migrate_then_readonly",
    "research_readonly_connection",
    "research_write_connection",
    "research_write_lock",
]
