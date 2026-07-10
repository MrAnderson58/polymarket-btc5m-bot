"""Market event Telegram alert configuration."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def alerts_enabled() -> bool:
    return os.getenv("ME_TELEGRAM_ALERTS_ENABLED", "false").lower() in ("1", "true", "yes")


def alert_shock_enabled() -> bool:
    return os.getenv("ME_ALERT_SHOCK", "true").lower() in ("1", "true", "yes")


def alert_reversal_enabled() -> bool:
    return os.getenv("ME_ALERT_REVERSAL", "true").lower() in ("1", "true", "yes")


def alert_paper_updates_enabled() -> bool:
    return os.getenv("ME_ALERT_PAPER_UPDATES", "false").lower() in ("1", "true", "yes")


def alert_ai_commentary_enabled() -> bool:
    return os.getenv("ME_ALERT_AI_COMMENTARY", "false").lower() in ("1", "true", "yes")


def alert_chat_id() -> str | None:
    """Dedicated alert chat; falls back to futures agent notify chat."""
    explicit = os.getenv("ME_ALERT_CHAT_ID", "").strip()
    if explicit:
        return explicit
    from bot.research.futures_agent.config import get_telegram_chat_id
    return get_telegram_chat_id()


ALERT_MAX_RETRIES = int(os.getenv("ME_ALERT_MAX_RETRIES", "2"))
ALERT_RETRY_DELAY_SEC = float(os.getenv("ME_ALERT_RETRY_DELAY_SEC", "1.0"))
