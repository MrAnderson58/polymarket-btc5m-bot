"""Serialize market_events DB startup (migrations + universe log)."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

from bot.research.market_events.config import BASE_DIR

STARTUP_LOCK_PATH = BASE_DIR / "data" / "market_events_startup.lock"


@contextlib.contextmanager
def market_events_startup_lock():
    """Exclusive lock so parallel collectors serialize migrations/universe INSERT."""
    STARTUP_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(STARTUP_LOCK_PATH), os.O_CREAT | os.O_RDWR)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
