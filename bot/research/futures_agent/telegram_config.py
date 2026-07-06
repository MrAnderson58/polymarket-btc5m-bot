"""Telegram inbound configuration for Futures Agent Stage 1b."""

from __future__ import annotations

import os

from bot.research.futures_agent.env_bootstrap import bootstrap_config


class TelegramInboundConfigError(RuntimeError):
    pass


def get_telegram_bot_token() -> str:
    bootstrap_config()
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def get_allowed_chat_ids() -> frozenset[int]:
    bootstrap_config()
    raw = os.getenv("TELEGRAM_AGENT_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return frozenset(ids)


def require_telegram_inbound_config() -> tuple[str, frozenset[int]]:
    """Fail fast if token or allowed chats missing."""
    token = get_telegram_bot_token()
    if not token:
        raise TelegramInboundConfigError(
            "TELEGRAM_BOT_TOKEN is required for Telegram inbound"
        )
    allowed = get_allowed_chat_ids()
    if not allowed:
        raise TelegramInboundConfigError(
            "TELEGRAM_AGENT_ALLOWED_CHAT_IDS is required (comma-separated chat IDs)"
        )
    return token, allowed


def is_chat_allowed(chat_id: int) -> bool:
    return chat_id in get_allowed_chat_ids()
