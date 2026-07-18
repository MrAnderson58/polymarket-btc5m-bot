"""Terminal Telegram screens — text only via components/formatters (no router markdown)."""

from __future__ import annotations

from typing import Any

from bot.terminal.alerts.models import AlertRule
from bot.terminal.decision.models import DecisionCard
from bot.terminal.portfolio.models import PortfolioIntelligence
from bot.terminal.models.dto import (
    AccountCard,
    HomeCard,
    MarketsCard,
    PortfolioCard,
    PositionCard,
    SignalCard,
    SystemStatusCard,
)
from bot.terminal.telegram.components import (
    CardRenderer,
    ProgressBarRenderer,
    SectionRenderer,
    StatusRenderer,
)
from bot.terminal.telegram.formatters import format_money, format_pct, format_duration


def render_start(card: HomeCard | None = None) -> str:
    online = True if card is None else card.system_online
    badge = "🟢 System Online" if online else "🔴 System Offline"
    return CardRenderer.render(
        "🤖 AI Trading Terminal",
        [
            SectionRenderer.render(badge, "Выберите раздел:"),
        ],
    )


def render_home(card: HomeCard) -> str:
    sections = [
        SectionRenderer.render(
            "System",
            StatusRenderer.render("online" if card.system_online else "offline"),
        ),
        SectionRenderer.render("Paper", str(card.mode)),
        SectionRenderer.render("Equity", format_money(card.equity, signed=False)),
        SectionRenderer.render("Balance", format_money(card.balance, signed=False)),
        SectionRenderer.render("Open Positions", str(card.open_positions)),
        SectionRenderer.render("Today's PnL", format_money(card.today_pnl)),
        SectionRenderer.render("Best Signal", card.best_signal or "—"),
        SectionRenderer.render("Last AI Decision", card.last_ai_decision or "—"),
        SectionRenderer.render("Workers", card.workers_line or "—"),
        SectionRenderer.render("Dashboard", card.dashboard_line or "—"),
    ]
    if card.system is not None:
        sections.append(render_system_status(card.system))
    return CardRenderer.render("🤖 AI Trading Terminal", sections)


def render_system_status(card: SystemStatusCard) -> str:
    worker_lines: list[str] = []
    for w in card.workers:
        worker_lines.append(
            f"{StatusRenderer.render('online' if w.online else 'offline')} {w.name}  {w.detail}"
        )
    if not worker_lines:
        worker_lines = ["—"]
    return SectionRenderer.render(
        "System Status",
        "Workers",
        *worker_lines,
        "",
        "Dashboard",
        card.dashboard or "—",
        "",
        "Telegram",
        card.telegram or "—",
        "",
        "Learning",
        card.learning or "—",
        "",
        "Decision",
        card.decision or "—",
        "",
        "Pattern",
        card.pattern or "—",
        "",
        "News",
        card.news or "—",
    )


def render_markets(card: MarketsCard) -> str:
    sections: list[str] = []
    if not card.groups:
        sections.append(card.summary or "No markets")
    else:
        for g in card.groups:
            sections.append(
                SectionRenderer.render(g.name, ", ".join(g.symbols) if g.symbols else "—")
            )
    return CardRenderer.render("🤖 AI Trading Terminal — Markets", sections)


def render_signals(signals: list[SignalCard]) -> str:
    usable = [s for s in signals if s.status != "unavailable"]
    sections: list[str] = [SectionRenderer.render("TOP 5")]
    if not usable:
        if signals and signals[0].status == "unavailable":
            sections.append(f"Unavailable: {signals[0].summary}")
        else:
            sections.append("No active signals")
        return CardRenderer.render("🤖 AI Trading Terminal — Signals", sections)

    for s in usable[:5]:
        conf_pct = None
        if s.confidence is not None:
            # G31 conf often 0–10 → map to %
            conf_pct = s.confidence * 10.0 if s.confidence <= 10 else float(s.confidence)
        sections.append(
            SectionRenderer.render(
                s.symbol,
                s.direction,
                f"Status  {s.status}",
                ProgressBarRenderer.render(conf_pct, label="Confidence"),
                s.summary or "",
            )
        )
    return CardRenderer.render("🤖 AI Trading Terminal — Signals", sections)


def render_signal(card: SignalCard | None = None) -> str:
    if card is None:
        return render_signals([])
    return render_signals([card])


def render_positions(positions: list[PositionCard]) -> str:
    usable = [p for p in positions if p.status != "unavailable"]
    sections: list[str] = []
    if not usable:
        if positions and positions[0].status == "unavailable":
            sections.append(f"Unavailable: {positions[0].extra.get('error', '')}")
        else:
            sections.append("No open positions")
        return CardRenderer.render("🤖 AI Trading Terminal — Positions", sections)

    for p in usable:
        risk_pct = None
        if p.entry and p.risk and p.entry > 0:
            risk_pct = min(100.0, (p.risk / p.entry) * 100.0)
        sections.append(
            SectionRenderer.render(
                p.symbol,
                p.side,
                f"PnL  {format_money(p.unrealized_pnl)} ({format_pct(p.unrealized_pnl_pct)})",
                f"Entry  {p.entry if p.entry is not None else '—'}",
                f"Size  {format_money(p.size, signed=False)}",
                ProgressBarRenderer.render(risk_pct, label="Risk"),
            )
        )
    return CardRenderer.render("🤖 AI Trading Terminal — Positions", sections)


def render_portfolio(card: PortfolioCard) -> str:
    """Legacy summary card — prefer render_portfolio_intelligence when available."""
    health_pct = card.winrate_pct
    return CardRenderer.render(
        "🤖 AI Trading Terminal — Portfolio",
        [
            SectionRenderer.render("Paper Equity", format_money(card.equity, signed=False)),
            SectionRenderer.render("Cash", format_money(card.cash, signed=False)),
            SectionRenderer.render("Used Margin", format_money(card.used_margin, signed=False)),
            SectionRenderer.render("Open Risk", format_money(card.open_risk, signed=False)),
            SectionRenderer.render("Today's PnL", format_money(card.today_pnl)),
            SectionRenderer.render("Week PnL", format_money(card.week_pnl)),
            SectionRenderer.render(
                "Winrate",
                ProgressBarRenderer.render(health_pct, label="Learning / Winrate"),
            ),
            SectionRenderer.render("Trades", str(card.trades)),
        ],
    )


def render_portfolio_intelligence(intel: PortfolioIntelligence) -> str:
    sector_lines: list[str] = []
    for s in intel.sector_exposure:
        syms = ", ".join(s.symbols) if s.symbols else "—"
        sector_lines.append(f"{s.label}  {s.weight_pct:.0f}%  ({syms})")
    if not sector_lines:
        sector_lines = ["—"]

    advice_lines: list[str] = []
    if intel.advice:
        advice_lines.append(intel.advice.headline)
        advice_lines.extend(intel.advice.bullets)
        advice_lines.append("")
        advice_lines.extend(intel.advice.actions)

    return CardRenderer.render(
        "🤖 AI Trading Terminal — Portfolio",
        [
            SectionRenderer.render(
                "Portfolio",
                f"Equity  {format_money(intel.equity, signed=False)}",
                f"Cash  {format_money(intel.cash, signed=False)}"
                + (f"  ({intel.cash_pct:.0f}%)" if intel.cash_pct is not None else ""),
                f"Open Risk  {format_money(intel.open_risk, signed=False)}"
                + (f"  ({intel.open_risk_pct:.0f}%)" if intel.open_risk_pct is not None else ""),
                f"Expected DD  {intel.expected_dd_pct:.0f}%" if intel.expected_dd_pct is not None else "Expected DD  —",
                f"Correlation  {intel.correlation:.0f}/100" if intel.correlation is not None else "Correlation  —",
                intel.correlation_note or "",
            ),
            SectionRenderer.render(
                "Sector Exposure",
                *sector_lines,
            ),
            SectionRenderer.render(
                "Crypto Exposure",
                ProgressBarRenderer.render(intel.crypto_exposure_pct, label=f"{intel.crypto_exposure_pct:.0f}% Crypto"),
            ),
            SectionRenderer.render("Portfolio Advice", *advice_lines)
            if advice_lines
            else SectionRenderer.render("Portfolio Advice", "—"),
        ],
    )


def render_account(card: AccountCard) -> str:
    return CardRenderer.render(
        "🤖 AI Trading Terminal — Account",
        [
            SectionRenderer.render("Mode", card.mode),
            SectionRenderer.render(
                "Balance / Equity",
                format_money(card.equity if card.equity is not None else card.balance, signed=False),
            ),
            SectionRenderer.render("Status", StatusRenderer.render(card.status)),
            card.summary or "",
        ],
    )


def render_settings() -> str:
    return CardRenderer.render(
        "🤖 AI Trading Terminal — Settings",
        [SectionRenderer.render("Settings", "coming soon")],
    )


def render_watchlist(symbols: list[str] | tuple[str, ...], *, notice: str | None = None) -> str:
    sections: list[str] = [SectionRenderer.render("Favorites")]
    if notice:
        sections.append(notice)
    if not symbols:
        sections.append("Empty — /watch add BTC")
    else:
        for i, sym in enumerate(symbols, start=1):
            sections.append(f"{i}. {sym}")
    sections.append(
        SectionRenderer.render(
            "Commands",
            "/watch",
            "/watch add BTC",
            "/watch remove ETH",
        )
    )
    return CardRenderer.render("🤖 AI Trading Terminal — Watchlist", sections)


def render_alerts(rules: list[AlertRule], *, notice: str | None = None) -> str:
    sections: list[str] = [SectionRenderer.render("Alerts")]
    if notice:
        sections.append(notice)
    if not rules:
        sections.append("No rules — /alert add BTC score>85")
    else:
        for r in rules:
            sections.append(
                SectionRenderer.render(
                    r.symbol,
                    r.kind.value,
                    r.describe(),
                    f"id  {r.rule_id}",
                )
            )
    sections.append(
        SectionRenderer.render(
            "Commands",
            "/alert",
            "/alert add BTC score>85",
            "/alert add NVDA LONG",
        )
    )
    return CardRenderer.render("🤖 AI Trading Terminal — Alerts", sections)


def render_decision(card: DecisionCard | None, *, notice: str | None = None) -> str:
    if card is None:
        sections = [notice or "No DecisionCard", "Usage: /decision BTC"]
        return CardRenderer.render("🤖 AI Trading Terminal — Decision", sections)

    levels = SectionRenderer.render(
        "Levels",
        f"Entry  {card.entry if card.entry is not None else '—'}",
        f"Stop   {card.stop if card.stop is not None else '—'}",
        f"TP1    {card.tp1 if card.tp1 is not None else '—'}",
        f"TP2    {card.tp2 if card.tp2 is not None else '—'}",
        f"Risk   {card.risk}",
        f"Hold   {card.holding_time}",
    )
    reasons = SectionRenderer.render("Reason", *card.reasons) if card.reasons else "Reason\n—"
    warnings = SectionRenderer.render("Warnings", *card.warnings) if card.warnings else ""
    summary = SectionRenderer.render("Summary", card.ai_summary)
    sections = [
        SectionRenderer.render(
            card.headline(),
            f"Direction  {card.direction}",
            f"Score  {card.score:.0f}",
            ProgressBarRenderer.render(card.confidence, label="Confidence"),
        ),
        levels,
        reasons,
        warnings,
        summary,
    ]
    if notice:
        sections.insert(0, notice)
    return CardRenderer.render("🤖 AI Trading Terminal — Decision", sections)


def render_why(explanation: Any) -> str:
    from bot.terminal.explain.scoring import ScoreExplanation

    if not isinstance(explanation, ScoreExplanation):
        return CardRenderer.render("Why", ["No explanation"])
    body = explanation.format_text()
    return CardRenderer.render(
        f"🤖 AI Trading Terminal — Why {explanation.symbol}",
        [body],
    )


def render_timeline(symbol: str, events: list[Any]) -> str:
    lines: list[str] = [symbol.upper(), ""]
    if not events:
        lines.append("No timeline events yet")
    else:
        for ev in events:
            ts = getattr(ev, "ts", "?")
            msg = getattr(ev, "message", str(ev))
            lines.append(f"{ts}")
            lines.append(msg)
            lines.append("")
    return CardRenderer.render("🤖 AI Trading Terminal — Timeline", ["\n".join(lines).rstrip()])


def render_execution_stub() -> str:
    return CardRenderer.render(
        "🚧 Execution API ready",
        [
            SectionRenderer.render(
                "Status",
                "Live execution not enabled.",
                "Paper adapter available.",
            ),
        ],
    )


__all__ = [
    "render_account",
    "render_alerts",
    "render_decision",
    "render_execution_stub",
    "render_home",
    "render_markets",
    "render_portfolio",
    "render_portfolio_intelligence",
    "render_positions",
    "render_settings",
    "render_signal",
    "render_signals",
    "render_start",
    "render_system_status",
    "render_timeline",
    "render_watchlist",
    "render_why",
]
