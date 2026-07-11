"""One-time Telegram configuration validation at collector startup."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def validate_telegram_config_at_startup() -> None:
    """Warn when bot token exists but alert chat ID cannot be resolved."""
    from bot.research.futures_agent.telegram_config import get_telegram_bot_token
    from bot.research.market_events.alert_config import resolve_alert_chat_id

    token = get_telegram_bot_token()
    if not token:
        return

    resolution = resolve_alert_chat_id()
    if resolution.chat_id:
        return

    reason = resolution.error or "Chat ID could not be resolved"
    logger.warning(
        "TELEGRAM CONFIG WARNING: bot token configured but chat ID unresolved — %s",
        reason,
    )
