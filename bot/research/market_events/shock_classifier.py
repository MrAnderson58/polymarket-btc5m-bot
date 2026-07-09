"""Market-wide vs asset-specific shock classification."""

from __future__ import annotations

from bot.research.market_events.event_types import (
    CLASSIFICATION_ASSET_SPECIFIC,
    CLASSIFICATION_MARKET_WIDE,
    CLASSIFICATION_SECTOR_WIDE,
    CLASSIFICATION_UNKNOWN,
)
from bot.research.market_events.shock_detector import ShockCandidate


def classify_shock(
    shock: ShockCandidate,
    *,
    eth_return_pct: float | None = None,
    median_universe_return_pct: float | None = None,
) -> str:
    asset = abs(shock.return_pct)
    btc = abs(shock.btc_return_pct or 0.0)
    eth = abs(eth_return_pct or 0.0)
    med = abs(median_universe_return_pct or 0.0)

    if shock.btc_return_pct is None and median_universe_return_pct is None:
        return CLASSIFICATION_UNKNOWN

    if btc >= 1.0 and asset >= btc * 0.7 and med >= 1.0:
        return CLASSIFICATION_MARKET_WIDE

    if eth >= 1.0 and asset >= eth * 0.6 and shock.symbol not in ("BTC", "ETH"):
        return CLASSIFICATION_SECTOR_WIDE

    rel = abs(shock.relative_return_pct or 0.0)
    if rel >= 0.8 and asset >= 1.0:
        return CLASSIFICATION_ASSET_SPECIFIC

    if asset >= 1.5 and btc < 0.5:
        return CLASSIFICATION_ASSET_SPECIFIC

    return CLASSIFICATION_UNKNOWN
