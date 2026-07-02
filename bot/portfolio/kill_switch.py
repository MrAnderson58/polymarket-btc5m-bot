"""Kill switch — blocks all live orders when active."""

from __future__ import annotations

from bot.config import BASE_DIR, LIVE_ENABLED

KILL_SWITCH_FILE = BASE_DIR / "LIVE_DISABLED"


def is_kill_switch_active() -> bool:
    if not LIVE_ENABLED:
        return True
    return KILL_SWITCH_FILE.exists()


def kill_switch_reason() -> str | None:
    if not LIVE_ENABLED:
        return "LIVE_ENABLED=false"
    if KILL_SWITCH_FILE.exists():
        return "LIVE_DISABLED file present"
    return None
