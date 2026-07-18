"""MorningBriefService — build / deliver daily Telegram brief."""

from __future__ import annotations

from bot.terminal.brief.builder import build_morning_brief, format_morning_brief
from bot.terminal.brief.models import MorningBrief


class MorningBriefService:
    def build(self, *, user_id: str | int = "default") -> MorningBrief:
        return build_morning_brief(user_id=str(user_id))

    def render(self, *, user_id: str | int = "default") -> str:
        return format_morning_brief(self.build(user_id=user_id))

    def deliver(
        self,
        chat_id: int | str,
        *,
        user_id: str | int | None = None,
        reply_markup: dict | None = None,
    ) -> bool:
        """Send morning brief via futures_agent Telegram helper."""
        uid = str(user_id if user_id is not None else chat_id)
        text = self.render(user_id=uid)
        try:
            from bot.research.futures_agent.responses import send_telegram_reply

            return bool(send_telegram_reply(int(chat_id), text, reply_markup=reply_markup))
        except Exception:
            return False


_default: MorningBriefService | None = None


def get_morning_brief_service() -> MorningBriefService:
    global _default
    if _default is None:
        _default = MorningBriefService()
    return _default


def reset_morning_brief_service() -> None:
    global _default
    _default = None


__all__ = [
    "MorningBriefService",
    "get_morning_brief_service",
    "reset_morning_brief_service",
]
