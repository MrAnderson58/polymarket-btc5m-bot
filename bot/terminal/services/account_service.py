"""AccountService — trading mode + paper equity from existing config/S4.2."""

from __future__ import annotations

from typing import Protocol

from bot.terminal.models.dto import AccountCard
from bot.terminal.services._db import market_events_ro


class AccountService(Protocol):
    def get_balance(self) -> AccountCard:
        ...


class DefaultAccountService:
    def get_balance(self) -> AccountCard:
        mode = "paper"
        try:
            from bot.config import TRADING_MODE

            mode = str(TRADING_MODE or "paper")
        except Exception:
            pass

        equity = None
        try:
            from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
                paper_performance_dashboard_s42,
            )

            with market_events_ro() as conn:
                dash = paper_performance_dashboard_s42(conn)
            equity = float(dash.get("current_equity") or 0.0)
        except Exception as exc:
            return AccountCard(
                mode=mode,
                balance=None,
                equity=None,
                status="degraded",
                summary=f"Mode={mode}; equity unavailable",
                extra={"error": str(exc), "source": "bot.config + s42 fallback"},
            )

        return AccountCard(
            mode=mode,
            balance=equity,
            equity=equity,
            status="ok",
            summary=f"Trading mode: {mode}",
            extra={"source": "bot.config.TRADING_MODE + paper_performance_dashboard_s42"},
        )

    def get_account(self) -> AccountCard:
        return self.get_balance()


def get_account_service() -> AccountService:
    return DefaultAccountService()


__all__ = ["AccountService", "DefaultAccountService", "get_account_service"]
