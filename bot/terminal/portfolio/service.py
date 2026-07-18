"""PortfolioIntelligenceService — analyze, don't just list trades."""

from __future__ import annotations

from bot.terminal.instruments.registry import InstrumentRegistry, get_instrument_registry
from bot.terminal.models.dto import PortfolioCard, PositionCard
from bot.terminal.portfolio.advice import build_advice, format_ai_summary
from bot.terminal.portfolio.exposure import (
    build_weights,
    crypto_exposure_pct,
    sector_exposure,
)
from bot.terminal.portfolio.models import PortfolioIntelligence
from bot.terminal.portfolio.risk import (
    cash_pct,
    correlation_score,
    expected_dd_pct,
    open_risk_pct,
)
from bot.terminal.services.portfolio_service import get_portfolio_service
from bot.terminal.services.positions_service import get_position_service


class PortfolioIntelligenceService:
    def __init__(
        self,
        *,
        registry: InstrumentRegistry | None = None,
    ) -> None:
        self._registry = registry or get_instrument_registry()

    def analyze(
        self,
        *,
        summary: PortfolioCard | None = None,
        positions: list[PositionCard] | None = None,
    ) -> PortfolioIntelligence:
        card = summary if summary is not None else get_portfolio_service().get_summary()
        pos = positions if positions is not None else get_position_service().get_open()

        equity = card.equity
        cash = card.cash
        open_risk = card.open_risk

        weights = build_weights(pos, equity=equity, registry=self._registry)
        sectors = sector_exposure(weights)
        crypto_pct = crypto_exposure_pct(weights)
        corr, corr_note = correlation_score(weights)
        risk_pct = open_risk_pct(open_risk, equity)
        c_pct = cash_pct(cash, equity)

        dd = expected_dd_pct(
            open_risk_pct=risk_pct,
            correlation=corr,
            crypto_pct=crypto_pct,
        )
        advice = build_advice(
            crypto_pct=crypto_pct,
            cash_pct=c_pct,
            expected_dd_pct=dd,
            weights=weights,
            sectors=sectors,
        )
        return PortfolioIntelligence(
            equity=equity,
            cash=cash,
            cash_pct=c_pct,
            open_risk=open_risk,
            open_risk_pct=risk_pct,
            expected_dd_pct=dd,
            correlation=corr,
            correlation_note=corr_note,
            sector_exposure=sectors,
            crypto_exposure_pct=crypto_pct,
            positions=weights,
            advice=advice,
            ai_summary=format_ai_summary(advice),
            extra={"source": "PortfolioIntelligenceService", "trades": card.trades},
        )


_default: PortfolioIntelligenceService | None = None


def get_portfolio_intelligence_service(
    *,
    registry: InstrumentRegistry | None = None,
) -> PortfolioIntelligenceService:
    global _default
    if registry is not None:
        return PortfolioIntelligenceService(registry=registry)
    if _default is None:
        _default = PortfolioIntelligenceService()
    return _default


def reset_portfolio_intelligence_service() -> None:
    global _default
    _default = None


__all__ = [
    "PortfolioIntelligenceService",
    "get_portfolio_intelligence_service",
    "reset_portfolio_intelligence_service",
]
