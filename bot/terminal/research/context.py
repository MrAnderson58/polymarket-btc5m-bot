"""Pack DecisionCard + Scanner + Portfolio + News + Learning for review."""

from __future__ import annotations

from bot.terminal.decision.models import DecisionCard, LearningHint
from bot.terminal.decision.service import get_decision_service
from bot.terminal.portfolio import get_portfolio_intelligence_service
from bot.terminal.research.models import ResearchContext
from bot.terminal.scanner.models import ScannerResult
from bot.terminal.scanner.scanner import get_scanner_service
from bot.terminal.services.portfolio_service import get_portfolio_service
from bot.terminal.services.system_service import get_system_service


def _decision_block(card: DecisionCard) -> str:
    reasons = "\n".join(card.reasons) if card.reasons else "—"
    warnings = "\n".join(card.warnings) if card.warnings else "—"
    return (
        f"{card.headline()}\n"
        f"score={card.score} confidence={card.confidence}\n"
        f"entry={card.entry} stop={card.stop} tp1={card.tp1} tp2={card.tp2}\n"
        f"risk={card.risk}\n"
        f"holding={card.holding_time}\n"
        f"reasons:\n{reasons}\n"
        f"warnings:\n{warnings}\n"
        f"template_summary:\n{card.ai_summary}"
    )


def _scanner_block(scan: ScannerResult | None) -> str:
    if scan is None:
        return "no scanner hit"
    c = scan.components
    return (
        f"{scan.symbol} {scan.direction} score={scan.score} provider={scan.provider}\n"
        f"confidence={scan.confidence}\n"
        f"components: trend={c.trend} volume={c.volume} ai={c.ai} "
        f"learning={c.learning} news={c.news} pattern={c.pattern}\n"
        f"reasons={list(scan.reasons)}"
    )


def _portfolio_block() -> str:
    try:
        intel = get_portfolio_intelligence_service().analyze()
        advice = ""
        if intel.advice:
            advice = " | ".join(intel.advice.actions[:4])
        return (
            f"crypto={intel.crypto_exposure_pct}% cash={intel.cash_pct}% "
            f"open_risk={intel.open_risk_pct}% corr={intel.correlation} "
            f"expected_dd={intel.expected_dd_pct}%\n"
            f"advice: {advice or intel.ai_summary}"
        )
    except Exception as exc:
        return f"portfolio unavailable: {exc}"


def _news_block(scan: ScannerResult | None) -> str:
    bits: list[str] = []
    if scan and scan.components.news is not None:
        bits.append(f"scanner_news_component={scan.components.news}")
    try:
        home = get_system_service().get_home()
        if home.system and home.system.news:
            bits.append(f"system_news={home.system.news}")
    except Exception:
        pass
    if scan and any("news" in r.lower() or "cpi" in r.lower() for r in scan.reasons):
        bits.extend(scan.reasons)
    return "\n".join(bits) if bits else "no dedicated news feed — use macro caution from profile"


def _learning_block(hint: LearningHint | None = None) -> str:
    if hint is not None:
        return (
            f"confirms={hint.confirms} winrate={hint.winrate_pct} note={hint.note}"
        )
    try:
        card = get_portfolio_service().get_summary()
        return f"paper_winrate={card.winrate_pct} trades={card.trades}"
    except Exception as exc:
        return f"learning unavailable: {exc}"


def build_research_context(
    symbol: str,
    *,
    decision: DecisionCard | None = None,
    scan: ScannerResult | None = None,
    learning: LearningHint | None = None,
    price: float | None = None,
) -> ResearchContext:
    sym = symbol.upper().replace("USDT", "")
    decision_svc = get_decision_service()
    card = decision or decision_svc.for_symbol(sym, price=price)
    if card is None:
        # Minimal placeholder so Claude still reviews "missing decision"
        from bot.terminal.instruments.models import AssetClass

        card = DecisionCard(
            symbol=sym,
            direction="—",
            confidence=None,
            score=0.0,
            entry=None,
            stop=None,
            tp1=None,
            tp2=None,
            risk="n/a",
            holding_time="n/a",
            reasons=("no DecisionCard",),
            warnings=("insufficient context",),
            ai_summary="No decision formed yet.",
            asset_class=AssetClass.CRYPTO_FUTURE,
        )
    scan_row = scan or get_scanner_service().by_symbol(sym)
    return ResearchContext(
        symbol=sym,
        decision_text=_decision_block(card),
        scanner_text=_scanner_block(scan_row),
        portfolio_text=_portfolio_block(),
        news_text=_news_block(scan_row),
        learning_text=_learning_block(learning),
        extra={"has_decision": decision is not None or card.score > 0},
    )


__all__ = ["build_research_context"]
