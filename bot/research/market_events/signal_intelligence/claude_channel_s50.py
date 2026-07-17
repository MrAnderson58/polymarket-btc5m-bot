"""S5.0 — Claude channel gate: telegram-only manual research terminal."""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from typing import Iterator

_CHANNEL = contextvars.ContextVar("claude_channel_s50", default="auto")

CLAUDE_CHANNEL_AUTO = "auto"
CLAUDE_CHANNEL_TELEGRAM = "telegram"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def s50_automatic_enabled() -> bool:
    return _env_bool("ME_S50_AUTOMATIC", default=False)


def s50_telegram_only() -> bool:
    return _env_bool("ME_S50_TELEGRAM_ONLY", default=True)


def s50_daily_limit() -> int:
    try:
        return max(1, int(os.getenv("ME_S50_DAILY_LIMIT", "30")))
    except (TypeError, ValueError):
        return 30


def s50_max_context_tokens() -> int:
    try:
        return max(1000, int(os.getenv("ME_S50_MAX_CONTEXT_TOKENS", "8000")))
    except (TypeError, ValueError):
        return 8000


def current_claude_channel() -> str:
    return _CHANNEL.get()


def claude_call_allowed() -> tuple[bool, str | None]:
    """Return (allowed, block_reason)."""
    if not s50_telegram_only():
        return True, None
    if current_claude_channel() == CLAUDE_CHANNEL_TELEGRAM:
        return True, None
    if s50_automatic_enabled():
        return True, None
    return False, "telegram_only: Claude is manual-only (S5.0). Use Telegram /ai /analyze /compare."


@contextmanager
def telegram_claude_session() -> Iterator[None]:
    """Mark the current thread as an allowed Telegram-initiated Claude call."""
    token = _CHANNEL.set(CLAUDE_CHANNEL_TELEGRAM)
    try:
        yield
    finally:
        _CHANNEL.reset(token)
