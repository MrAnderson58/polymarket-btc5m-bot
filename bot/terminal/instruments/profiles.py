"""Market Profile Engine — descriptive market characteristics (not trading logic)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bot.terminal.instruments.models import AssetClass, Instrument


@dataclass(frozen=True)
class MarketProfile:
    """Describes how a market behaves — hours, funding, macro sensitivities."""

    asset_class: AssetClass
    is_24_7: bool
    trading_hours: str
    has_funding: bool
    has_premarket: bool
    has_postmarket: bool
    macro_sensitive: bool
    uses_earnings: bool
    uses_options: bool
    uses_liquidations: bool
    notes: str = ""


class MarketProfileProvider(Protocol):
    def profile_for(self, asset_class: AssetClass) -> MarketProfile:
        """Return the profile for an asset class."""
        ...


class CryptoFutureProfile:
    """Perpetual / crypto futures: 24/7, funding, liquidations."""

    def profile(self) -> MarketProfile:
        return MarketProfile(
            asset_class=AssetClass.CRYPTO_FUTURE,
            is_24_7=True,
            trading_hours="24/7",
            has_funding=True,
            has_premarket=False,
            has_postmarket=False,
            macro_sensitive=True,
            uses_earnings=False,
            uses_options=False,
            uses_liquidations=True,
            notes="Perpetual futures with funding and liquidation risk",
        )


class CryptoProfile:
    """Spot crypto: 24/7, no classic funding."""

    def profile(self) -> MarketProfile:
        return MarketProfile(
            asset_class=AssetClass.CRYPTO,
            is_24_7=True,
            trading_hours="24/7",
            has_funding=False,
            has_premarket=False,
            has_postmarket=False,
            macro_sensitive=True,
            uses_earnings=False,
            uses_options=False,
            uses_liquidations=False,
            notes="Spot crypto",
        )


class StockProfile:
    """Equity cash session with pre/post market."""

    def profile(self) -> MarketProfile:
        return MarketProfile(
            asset_class=AssetClass.STOCK,
            is_24_7=False,
            trading_hours="RTH 09:30–16:00 ET",
            has_funding=False,
            has_premarket=True,
            has_postmarket=True,
            macro_sensitive=True,
            uses_earnings=True,
            uses_options=True,
            uses_liquidations=False,
            notes="Listed equities",
        )


class EtfProfile:
    """ETF — equity-like hours, options common."""

    def profile(self) -> MarketProfile:
        return MarketProfile(
            asset_class=AssetClass.ETF,
            is_24_7=False,
            trading_hours="RTH 09:30–16:00 ET",
            has_funding=False,
            has_premarket=True,
            has_postmarket=True,
            macro_sensitive=True,
            uses_earnings=False,
            uses_options=True,
            uses_liquidations=False,
            notes="Exchange-traded funds",
        )


class IndexProfile:
    """Index — cash session; often quoted continuously elsewhere."""

    def profile(self) -> MarketProfile:
        return MarketProfile(
            asset_class=AssetClass.INDEX,
            is_24_7=False,
            trading_hours="Cash equity hours / futures vary",
            has_funding=False,
            has_premarket=False,
            has_postmarket=False,
            macro_sensitive=True,
            uses_earnings=False,
            uses_options=True,
            uses_liquidations=False,
            notes="Index benchmarks",
        )


class CommodityProfile:
    """Commodities — session-based, macro sensitive."""

    def profile(self) -> MarketProfile:
        return MarketProfile(
            asset_class=AssetClass.COMMODITY,
            is_24_7=False,
            trading_hours="Nearly continuous futures (venue-specific)",
            has_funding=False,
            has_premarket=False,
            has_postmarket=False,
            macro_sensitive=True,
            uses_earnings=False,
            uses_options=True,
            uses_liquidations=False,
            notes="Commodities / metals / energy",
        )


class ForexProfile:
    """FX — nearly 24/5, macro heavy."""

    def profile(self) -> MarketProfile:
        return MarketProfile(
            asset_class=AssetClass.FOREX,
            is_24_7=False,
            trading_hours="24/5 (Sun open – Fri close)",
            has_funding=False,
            has_premarket=False,
            has_postmarket=False,
            macro_sensitive=True,
            uses_earnings=False,
            uses_options=True,
            uses_liquidations=False,
            notes="Spot FX",
        )


_PROFILES: dict[AssetClass, MarketProfile] = {
    AssetClass.CRYPTO: CryptoProfile().profile(),
    AssetClass.CRYPTO_FUTURE: CryptoFutureProfile().profile(),
    AssetClass.STOCK: StockProfile().profile(),
    AssetClass.ETF: EtfProfile().profile(),
    AssetClass.INDEX: IndexProfile().profile(),
    AssetClass.COMMODITY: CommodityProfile().profile(),
    AssetClass.FOREX: ForexProfile().profile(),
}


def get_market_profile(asset_class: AssetClass | str) -> MarketProfile:
    """Return market profile for an asset class."""
    if isinstance(asset_class, str):
        asset_class = AssetClass(asset_class.strip().lower())
    return _PROFILES[asset_class]


def get_market_profile_for_instrument(instrument: Instrument) -> MarketProfile:
    """Return market profile for a registered instrument."""
    return get_market_profile(instrument.asset_class)


def list_market_profiles() -> list[MarketProfile]:
    """All known asset-class profiles."""
    return [get_market_profile(ac) for ac in AssetClass]


__all__ = [
    "CommodityProfile",
    "CryptoFutureProfile",
    "CryptoProfile",
    "EtfProfile",
    "ForexProfile",
    "IndexProfile",
    "MarketProfile",
    "StockProfile",
    "get_market_profile",
    "get_market_profile_for_instrument",
    "list_market_profiles",
]
