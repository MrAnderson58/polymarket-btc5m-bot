"""Market event Telegram alert configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ChatIdResolution:
    chat_id: str | None
    source: str | None
    error: str | None = None


def _parse_allowed_chat_ids(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def resolve_alert_chat_id() -> ChatIdResolution:
    """Deterministic chat ID resolution for market event Telegram alerts."""
    explicit = os.getenv("ME_ALERT_CHAT_ID", "").strip()
    if explicit:
        return ChatIdResolution(explicit, "ME_ALERT_CHAT_ID")

    agent = os.getenv("TELEGRAM_AGENT_CHAT_ID", "").strip()
    if agent:
        return ChatIdResolution(agent, "TELEGRAM_AGENT_CHAT_ID")

    allowed_raw = os.getenv("TELEGRAM_AGENT_ALLOWED_CHAT_IDS", "").strip()
    if allowed_raw:
        ids = _parse_allowed_chat_ids(allowed_raw)
        if len(ids) == 1:
            return ChatIdResolution(ids[0], "TELEGRAM_AGENT_ALLOWED_CHAT_IDS")
        if len(ids) > 1:
            return ChatIdResolution(
                None,
                None,
                "Multiple TELEGRAM_AGENT_ALLOWED_CHAT_IDS configured; set ME_ALERT_CHAT_ID explicitly",
            )

    return ChatIdResolution(None, None, "No chat ID configured")


def alert_chat_id() -> str | None:
    return resolve_alert_chat_id().chat_id


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


ALERT_MAX_RETRIES = int(os.getenv("ME_ALERT_MAX_RETRIES", "2"))
ALERT_RETRY_DELAY_SEC = float(os.getenv("ME_ALERT_RETRY_DELAY_SEC", "1.0"))
ALERT_RETRY_BACKOFF_MULTIPLIER = float(os.getenv("ME_ALERT_RETRY_BACKOFF_MULTIPLIER", "2.0"))
ALERT_RETRY_MAX_DELAY_SEC = float(os.getenv("ME_ALERT_RETRY_MAX_DELAY_SEC", "30.0"))


def alert_locale() -> str:
    """Alert text locale: en | ru."""
    return os.getenv("ME_ALERT_LOCALE", "ru").strip().lower()[:2] or "en"
