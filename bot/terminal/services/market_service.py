"""MarketService — instrument lists from existing project constants/registry."""

from __future__ import annotations

from typing import Protocol

from bot.terminal.models.dto import MarketGroupCard, MarketsCard


class MarketService(Protocol):
    def get_markets(self) -> MarketsCard:
        ...


class DefaultMarketService:
    def get_markets(self) -> MarketsCard:
        try:
            from bot.research.market_events.instrument_types import (
                COMMODITY_CANDIDATES,
                CRYPTO_CANDIDATES,
                EQUITY_CANDIDATES,
                INDEX_CANDIDATES,
            )

            groups = (
                MarketGroupCard(name="Crypto", symbols=tuple(CRYPTO_CANDIDATES)),
                MarketGroupCard(name="TradFi", symbols=tuple(EQUITY_CANDIDATES)),
                MarketGroupCard(name="Commodities", symbols=tuple(COMMODITY_CANDIDATES)),
                MarketGroupCard(name="Indices", symbols=tuple(INDEX_CANDIDATES)),
            )
            return MarketsCard(
                groups=groups,
                summary="Existing instrument candidate lists",
            )
        except Exception as exc:
            return MarketsCard(
                groups=(),
                summary=f"Markets unavailable: {exc}",
            )

    def get_active_market_summary(self) -> dict:
        """Legacy V6.0.0 helper — prefer get_markets()."""
        card = self.get_markets()
        return {
            "status": "ok" if card.groups else "unavailable",
            "groups": [g.name for g in card.groups],
            "source": "instrument_types",
        }


def get_market_service() -> MarketService:
    return DefaultMarketService()


__all__ = ["DefaultMarketService", "MarketService", "get_market_service"]
