"""Versioned asset-class shock profiles — shadow/research only, not production SHOCK_A-E."""

from __future__ import annotations

from dataclasses import dataclass, field

PROFILE_CRYPTO = "CRYPTO"
PROFILE_COMMODITY_OIL = "COMMODITY_OIL"
PROFILE_COMMODITY_GOLD = "COMMODITY_GOLD"
PROFILE_COMMODITY_SILVER = "COMMODITY_SILVER"
PROFILE_EQUITY_SINGLE = "EQUITY_SINGLE_NAME"
PROFILE_ETF_INDEX = "ETF_INDEX_PROXY"

PROFILE_VERSION = "e31_descriptive_v1"

_SYMBOL_PROFILE_MAP: dict[str, str] = {
    "BTC": PROFILE_CRYPTO, "ETH": PROFILE_CRYPTO, "SOL": PROFILE_CRYPTO,
    "XRP": PROFILE_CRYPTO, "BNB": PROFILE_CRYPTO, "DOGE": PROFILE_CRYPTO,
    "ADA": PROFILE_CRYPTO, "LINK": PROFILE_CRYPTO, "AVAX": PROFILE_CRYPTO, "SUI": PROFILE_CRYPTO,
    "GOLD": PROFILE_COMMODITY_GOLD, "XAU": PROFILE_COMMODITY_GOLD,
    "OIL": PROFILE_COMMODITY_OIL, "CL": PROFILE_COMMODITY_OIL,
    "SILVER": PROFILE_COMMODITY_SILVER, "XAG": PROFILE_COMMODITY_SILVER,
    "NVDA": PROFILE_EQUITY_SINGLE, "TSLA": PROFILE_EQUITY_SINGLE, "META": PROFILE_EQUITY_SINGLE,
    "AAPL": PROFILE_EQUITY_SINGLE, "AMZN": PROFILE_EQUITY_SINGLE, "MSFT": PROFILE_EQUITY_SINGLE,
    "GOOGL": PROFILE_EQUITY_SINGLE,
    "NASDAQ100_PROXY": PROFILE_ETF_INDEX, "SP500_PROXY": PROFILE_ETF_INDEX, "QQQ": PROFILE_ETF_INDEX,
}


@dataclass(frozen=True)
class ShockProfile:
    name: str
    windows_pct: dict[int, float]
    min_abs_return_pct: float
    notes: str = ""


# Descriptive initial thresholds from E.3 audit evidence — NOT optimized on sample.
SHOCK_PROFILES: dict[str, ShockProfile] = {
    PROFILE_CRYPTO: ShockProfile(
        PROFILE_CRYPTO,
        {30: 1.5, 60: 2.0, 180: 3.0},
        min_abs_return_pct=1.0,
        notes="Production SHOCK_A-E unchanged; profile for shadow comparison only",
    ),
    PROFILE_COMMODITY_OIL: ShockProfile(
        PROFILE_COMMODITY_OIL,
        {30: 0.75, 60: 1.0, 180: 1.0},
        min_abs_return_pct=0.5,
        notes="Audit: one ~1% episode at 30s and 180s",
    ),
    PROFILE_COMMODITY_GOLD: ShockProfile(
        PROFILE_COMMODITY_GOLD,
        {30: 0.5, 60: 0.75, 180: 1.0},
        min_abs_return_pct=0.4,
        notes="Tight spreads; lower threshold than crypto",
    ),
    PROFILE_COMMODITY_SILVER: ShockProfile(
        PROFILE_COMMODITY_SILVER,
        {30: 0.5, 60: 0.75, 180: 1.0},
        min_abs_return_pct=0.5,
        notes="Audit: one ~0.5% / 30s up episode",
    ),
    PROFILE_EQUITY_SINGLE: ShockProfile(
        PROFILE_EQUITY_SINGLE,
        {30: 0.5, 60: 0.75, 180: 1.5},
        min_abs_return_pct=0.5,
        notes="Audit: META ~0.5% / 30s down; NVDA higher beta",
    ),
    PROFILE_ETF_INDEX: ShockProfile(
        PROFILE_ETF_INDEX,
        {30: 0.4, 60: 0.6, 180: 1.0},
        min_abs_return_pct=0.35,
        notes="QQQ lower vol than single names",
    ),
}


def profile_for_symbol(symbol: str) -> ShockProfile:
    name = _SYMBOL_PROFILE_MAP.get(symbol.upper(), PROFILE_CRYPTO)
    return SHOCK_PROFILES[name]


def profile_name_for_symbol(symbol: str) -> str:
    return profile_for_symbol(symbol).name
