"""ResearchService — review formed decisions (Claude when available)."""

from __future__ import annotations

from bot.terminal.brief.builder import build_morning_brief
from bot.terminal.brief.models import MorningBrief
from bot.terminal.research.claude_review import claude_review, format_research_review, template_review
from bot.terminal.research.context import build_research_context
from bot.terminal.research.models import ResearchContext, ResearchReview


class ResearchService:
    """V7.0.3 — Claude reviews packed Terminal context; never blank-slate."""

    def review_symbol(
        self,
        symbol: str,
        *,
        use_claude: bool = True,
        price: float | None = None,
    ) -> ResearchReview:
        ctx = build_research_context(symbol, price=price)
        if not use_claude:
            return template_review(ctx)
        return claude_review(ctx)

    def review_context(self, ctx: ResearchContext, *, use_claude: bool = True) -> ResearchReview:
        if not use_claude:
            return template_review(ctx)
        return claude_review(ctx)

    def enrich_brief(
        self,
        brief: MorningBrief | None = None,
        *,
        user_id: str | int = "default",
        use_claude: bool = True,
    ) -> MorningBrief:
        """Optional: Claude short AI Summary over an already-built Morning Brief."""
        base = brief or build_morning_brief(user_id=str(user_id))
        if not use_claude:
            return base
        # Review top opportunity if present
        top_line = base.top_opportunities.lines[0] if base.top_opportunities.lines else ""
        symbol = top_line.split()[0] if top_line else "BTC"
        review = self.review_symbol(symbol, use_claude=True)
        summary = (
            f"{base.ai_summary}\n\n"
            f"Claude review ({review.verdict}): {review.summary}"
        )
        return MorningBrief(
            greeting=base.greeting,
            generated_at=base.generated_at,
            markets=base.markets,
            stocks=base.stocks,
            crypto=base.crypto,
            macro=base.macro,
            top_opportunities=base.top_opportunities,
            portfolio_advice=base.portfolio_advice,
            watchlist_updates=base.watchlist_updates,
            ai_summary=summary,
            user_id=base.user_id,
            extra={**dict(base.extra), "research_verdict": review.verdict, "research_provider": review.provider},
        )


_default: ResearchService | None = None


def get_research_service() -> ResearchService:
    global _default
    if _default is None:
        _default = ResearchService()
    return _default


def reset_research_service() -> None:
    global _default
    _default = None


__all__ = [
    "ResearchService",
    "format_research_review",
    "get_research_service",
    "reset_research_service",
]
