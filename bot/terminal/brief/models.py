"""Morning Brief DTOs (V7.0.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class BriefSection:
    title: str
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class MorningBrief:
    """Daily Telegram morning package."""

    greeting: str
    generated_at: str
    markets: BriefSection
    stocks: BriefSection
    crypto: BriefSection
    macro: BriefSection
    top_opportunities: BriefSection
    portfolio_advice: BriefSection
    watchlist_updates: BriefSection
    ai_summary: str
    user_id: str = "default"
    extra: dict[str, Any] = field(default_factory=dict)

    def sections(self) -> tuple[BriefSection, ...]:
        return (
            BriefSection(title="Good Morning", lines=(self.greeting,)),
            self.markets,
            self.stocks,
            self.crypto,
            self.macro,
            self.top_opportunities,
            self.portfolio_advice,
            self.watchlist_updates,
            BriefSection(title="AI Summary", lines=tuple(self.ai_summary.splitlines())),
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


__all__ = ["BriefSection", "MorningBrief", "utc_now_iso"]
