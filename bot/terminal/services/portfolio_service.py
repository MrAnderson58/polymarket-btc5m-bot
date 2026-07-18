"""PortfolioService — paper account summary from existing S4.2 dashboard."""

from __future__ import annotations

from typing import Protocol

from bot.terminal.models.dto import PortfolioCard
from bot.terminal.services._db import market_events_ro


class PortfolioService(Protocol):
    def get_summary(self) -> PortfolioCard:
        ...


class DefaultPortfolioService:
    def get_summary(self) -> PortfolioCard:
        try:
            from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
                CAPITAL_PER_TRADE_USD,
                paper_performance_dashboard_s42,
            )

            with market_events_ro() as conn:
                dash = paper_performance_dashboard_s42(conn)
            equity = float(dash.get("current_equity") or 0.0)
            open_n = int(dash.get("open_trades") or 0)
            used = round(open_n * float(CAPITAL_PER_TRADE_USD), 2)
            cash = round(max(0.0, equity - used), 2)
            return PortfolioCard(
                equity=equity,
                cash=cash,
                used_margin=used,
                open_risk=used,
                open_positions=open_n,
                today_pnl=float(dash.get("today_pnl_usd") or 0.0),
                week_pnl=float(dash.get("weekly_pnl_usd") or 0.0),
                winrate_pct=float(dash.get("winrate_pct") or 0.0),
                trades=int(dash.get("trades") or 0),
                summary="S4.2 paper account",
                extra={
                    "source": "paper_performance_dashboard_s42",
                    "capital_per_trade": dash.get("capital_per_trade"),
                    "leverage": dash.get("leverage"),
                    "pending_settlement": dash.get("pending_settlement"),
                },
            )
        except Exception as exc:
            return PortfolioCard(
                summary="Portfolio unavailable",
                extra={"error": str(exc), "source": "fallback"},
            )

    # Back-compat alias used by V6.0.0
    def get_portfolio(self) -> PortfolioCard:
        return self.get_summary()


def get_portfolio_service() -> PortfolioService:
    return DefaultPortfolioService()


__all__ = ["DefaultPortfolioService", "PortfolioService", "get_portfolio_service"]
