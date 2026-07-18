"""Instrument data providers — StaticProvider only in V6.1.1."""

from __future__ import annotations

from typing import Protocol, Sequence

from bot.terminal.instruments.models import AssetClass, Instrument


class InstrumentProvider(Protocol):
    """Source of instrument definitions (static today; venue APIs later)."""

    def load(self) -> Sequence[Instrument]:
        """Return all instruments from this provider."""
        ...


class StaticProvider:
    """Hard-coded seed catalog — no network, no SQL."""

    def load(self) -> Sequence[Instrument]:
        """Load the static seed instrument list."""
        return (
            Instrument(
                symbol="BTCUSDT",
                name="Bitcoin",
                asset_class=AssetClass.CRYPTO_FUTURE,
                exchange="binance",
                currency="USDT",
                tick_size=0.1,
                price_precision=1,
                quantity_precision=3,
            ),
            Instrument(
                symbol="ETHUSDT",
                name="Ethereum",
                asset_class=AssetClass.CRYPTO_FUTURE,
                exchange="binance",
                currency="USDT",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=3,
            ),
            Instrument(
                symbol="SOLUSDT",
                name="Solana",
                asset_class=AssetClass.CRYPTO_FUTURE,
                exchange="binance",
                currency="USDT",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=2,
            ),
            Instrument(
                symbol="NVDA",
                name="NVIDIA Corp",
                asset_class=AssetClass.STOCK,
                exchange="NASDAQ",
                currency="USD",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=0,
            ),
            Instrument(
                symbol="AAPL",
                name="Apple Inc",
                asset_class=AssetClass.STOCK,
                exchange="NASDAQ",
                currency="USD",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=0,
            ),
            Instrument(
                symbol="META",
                name="Meta Platforms",
                asset_class=AssetClass.STOCK,
                exchange="NASDAQ",
                currency="USD",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=0,
            ),
            Instrument(
                symbol="QQQ",
                name="Invesco QQQ Trust",
                asset_class=AssetClass.ETF,
                exchange="NASDAQ",
                currency="USD",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=0,
            ),
            Instrument(
                symbol="SPY",
                name="SPDR S&P 500 ETF",
                asset_class=AssetClass.ETF,
                exchange="NYSE",
                currency="USD",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=0,
            ),
            Instrument(
                symbol="GLD",
                name="SPDR Gold Shares",
                asset_class=AssetClass.ETF,
                exchange="NYSE",
                currency="USD",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=0,
            ),
            Instrument(
                symbol="XAUUSD",
                name="Gold Spot",
                asset_class=AssetClass.COMMODITY,
                exchange="OTC",
                currency="USD",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=2,
            ),
            Instrument(
                symbol="CL",
                name="Crude Oil",
                asset_class=AssetClass.COMMODITY,
                exchange="NYMEX",
                currency="USD",
                tick_size=0.01,
                price_precision=2,
                quantity_precision=0,
            ),
            Instrument(
                symbol="EURUSD",
                name="Euro / US Dollar",
                asset_class=AssetClass.FOREX,
                exchange="FX",
                currency="USD",
                tick_size=0.00001,
                price_precision=5,
                quantity_precision=2,
            ),
        )


__all__ = ["InstrumentProvider", "StaticProvider"]
