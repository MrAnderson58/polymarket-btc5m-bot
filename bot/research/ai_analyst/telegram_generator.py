"""S46 Telegram post generator (thin wrapper for profile-based generation)."""

from __future__ import annotations

from typing import Any

from bot.research.ai_analyst.config import DEFAULT_AGENTS
from bot.research.ai_analyst.report_generator import generate_artifact


def generate_telegram_post(*, context: dict[str, Any], client: Any, **kwargs: Any) -> dict[str, Any]:
    return generate_artifact(
        profile=DEFAULT_AGENTS["s46_telegram"],
        context=context,
        client=client,
        **kwargs,
    )
