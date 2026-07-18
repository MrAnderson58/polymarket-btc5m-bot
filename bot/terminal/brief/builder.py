"""Assemble Morning Brief from Terminal services (no Claude required)."""

from __future__ import annotations

from bot.terminal.brief.models import BriefSection, MorningBrief, utc_now_iso
from bot.terminal.decision import DecisionCard, get_decision_service
from bot.terminal.portfolio import get_portfolio_intelligence_service
from bot.terminal.scanner import get_scanner_service
from bot.terminal.services.market_service import get_market_service
from bot.terminal.watchlist import get_watchlist_service


def _lines(*parts: str) -> tuple[str, ...]:
    return tuple(p for p in parts if p)


def _markets_sections() -> tuple[BriefSection, BriefSection, BriefSection, BriefSection]:
    card = get_market_service().get_markets()
    by_name = {g.name.lower(): g for g in card.groups}

    def _syms(key: str, fallback: str = "—") -> str:
        g = by_name.get(key)
        if not g or not g.symbols:
            return fallback
        return ", ".join(g.symbols[:8])

    markets = BriefSection(
        title="Markets",
        lines=_lines(
            f"Groups: {', '.join(g.name for g in card.groups) or '—'}",
            card.summary or "",
        ),
    )
    stocks = BriefSection(
        title="Stocks",
        lines=_lines(
            _syms("tradfi") if "tradfi" in by_name else _syms("stocks", "NVDA, AAPL, META…"),
            "RTH focus · earnings / options risk on",
        ),
    )
    crypto = BriefSection(
        title="Crypto",
        lines=_lines(
            _syms("crypto", "BTC, ETH, SOL…"),
            "24/7 · funding + liquidation regime",
        ),
    )
    macro = BriefSection(
        title="Macro",
        lines=_lines(
            "Watch rates / USD / CPI window",
            "Commodities & indices as risk proxy",
            _syms("commodities", "GOLD, CL"),
        ),
    )
    return markets, stocks, crypto, macro


def _top_opportunities(limit: int = 3) -> BriefSection:
    lines: list[str] = []
    try:
        cards = get_decision_service().top(limit)
        for c in cards:
            lines.append(f"{c.symbol} {c.direction} · score {c.score:.0f}")
            if c.reasons:
                lines.append(f"  {c.reasons[0]}")
    except Exception:
        rows = get_scanner_service().top(limit)
        for r in rows:
            lines.append(f"{r.symbol} {r.direction} · score {r.score:.0f}")
    if not lines:
        lines = ["No high-conviction setups yet"]
    return BriefSection(title="Top Opportunities", lines=tuple(lines))


def _portfolio_advice_section(user_id: str) -> BriefSection:
    try:
        intel = get_portfolio_intelligence_service().analyze()
        lines: list[str] = []
        if intel.advice:
            lines.append(intel.advice.headline)
            lines.extend(intel.advice.bullets[:3])
            lines.extend(intel.advice.actions[:3])
        if not lines:
            lines = [intel.ai_summary or "Portfolio quiet"]
        return BriefSection(title="Portfolio Advice", lines=tuple(lines))
    except Exception as exc:
        return BriefSection(title="Portfolio Advice", lines=(f"Unavailable: {exc}",))


def _watchlist_section(user_id: str) -> BriefSection:
    try:
        symbols = list(get_watchlist_service().list_symbols(user_id))
        scanner = get_scanner_service()
        lines: list[str] = []
        for sym in symbols[:8]:
            hit = scanner.by_symbol(sym)
            if hit:
                lines.append(f"{sym} · {hit.direction} · score {hit.score:.0f}")
            else:
                lines.append(f"{sym} · no scan hit")
        if not lines:
            lines = ["Watchlist empty — /watch add BTC"]
        return BriefSection(title="Watchlist Updates", lines=tuple(lines))
    except Exception as exc:
        return BriefSection(title="Watchlist Updates", lines=(f"Unavailable: {exc}",))


def template_ai_summary(
    *,
    opportunities: BriefSection,
    portfolio: BriefSection,
    watchlist: BriefSection,
) -> str:
    top = opportunities.lines[0] if opportunities.lines else "—"
    port = portfolio.lines[0] if portfolio.lines else "—"
    watch = watchlist.lines[0] if watchlist.lines else "—"
    return (
        f"Focus: {top}\n"
        f"Book: {port}\n"
        f"Watch: {watch}\n"
        "Bias: trade the plan, not the noise. Size down into macro prints."
    )


def build_morning_brief(
    *,
    user_id: str = "default",
    greeting: str | None = None,
    ai_summary: str | None = None,
) -> MorningBrief:
    markets, stocks, crypto, macro = _markets_sections()
    top = _top_opportunities()
    portfolio = _portfolio_advice_section(user_id)
    watch = _watchlist_section(user_id)
    summary = ai_summary or template_ai_summary(
        opportunities=top, portfolio=portfolio, watchlist=watch
    )
    greet = greeting or "Good Morning — here is your Terminal brief."
    return MorningBrief(
        greeting=greet,
        generated_at=utc_now_iso(),
        markets=markets,
        stocks=stocks,
        crypto=crypto,
        macro=macro,
        top_opportunities=top,
        portfolio_advice=portfolio,
        watchlist_updates=watch,
        ai_summary=summary,
        user_id=str(user_id),
    )


def format_morning_brief(brief: MorningBrief) -> str:
    parts: list[str] = [
        "☀️ Good Morning",
        brief.generated_at,
        "",
        brief.greeting,
        "",
    ]
    for section in (
        brief.markets,
        brief.stocks,
        brief.crypto,
        brief.macro,
        brief.top_opportunities,
        brief.portfolio_advice,
        brief.watchlist_updates,
    ):
        parts.append(section.title)
        parts.append("")
        parts.extend(section.lines or ("—",))
        parts.append("")
    parts.append("AI Summary")
    parts.append("")
    parts.append(brief.ai_summary)
    return "\n".join(parts).rstrip()


__all__ = [
    "build_morning_brief",
    "format_morning_brief",
    "template_ai_summary",
]
