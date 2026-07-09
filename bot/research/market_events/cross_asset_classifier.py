"""Cross-asset shock classification — configurable rules, not optimized."""

from __future__ import annotations

from bot.research.market_events.instrument_types import (
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_INDEX,
    CROSS_ASSET_SPECIFIC,
    CROSS_COMMODITY_GEOPOLITICAL,
    CROSS_CRYPTO_MARKET_WIDE,
    CROSS_EQUITY_MARKET_WIDE,
    CROSS_TECH_SECTOR_WIDE,
    CROSS_TOKENIZED_DISLOCATION,
    CROSS_UNKNOWN,
)
from bot.research.market_events.shock_detector import ShockCandidate

# Fixed research thresholds (not optimized on sample)
DISLOCATION_BASIS_BPS = 150.0
DISLOCATION_REF_RETURN_MAX = 0.5
TECH_TICKERS = frozenset({"NVDA", "TSLA", "META", "AAPL", "MSFT", "GOOGL", "AMZN"})
TECH_MOVE_MIN = 2.0
TECH_BREADTH_MIN = 2


def classify_cross_asset(
    shock: ShockCandidate,
    *,
    asset_class: str,
    canonical_asset: str,
    basis_bps: float | None,
    reference_return_pct: float | None,
    peer_returns: dict[str, float],
    btc_return_pct: float | None,
    median_crypto_return: float | None,
) -> str:
    asset_move = abs(shock.return_pct)
    ref_move = abs(reference_return_pct or 0.0)

    if basis_bps is not None and abs(basis_bps) >= DISLOCATION_BASIS_BPS:
        if ref_move <= DISLOCATION_REF_RETURN_MAX and asset_move >= 2.0:
            return CROSS_TOKENIZED_DISLOCATION

    if asset_class == ASSET_CLASS_CRYPTO:
        btc = abs(btc_return_pct or 0.0)
        med = abs(median_crypto_return or 0.0)
        if btc >= 1.0 and asset_move >= btc * 0.7 and med >= 1.0:
            return CROSS_CRYPTO_MARKET_WIDE
        if asset_move >= 1.5 and btc < 0.5:
            return CROSS_ASSET_SPECIFIC
        return CROSS_UNKNOWN

    if asset_class in (ASSET_CLASS_EQUITY, ASSET_CLASS_INDEX):
        tech_moves = [
            abs(peer_returns[s]) for s in peer_returns
            if s in TECH_TICKERS and peer_returns[s] is not None
        ]
        tech_down = sum(1 for m in tech_moves if m >= TECH_MOVE_MIN)
        if tech_down >= TECH_BREADTH_MIN:
            return CROSS_TECH_SECTOR_WIDE
        index_moves = [abs(v) for k, v in peer_returns.items() if k.endswith("_PROXY")]
        if index_moves and asset_move >= 1.5 and max(index_moves) >= 1.0:
            return CROSS_EQUITY_MARKET_WIDE
        if asset_move >= 2.0 and max(peer_returns.values(), key=abs, default=0) < 0.5:
            return CROSS_ASSET_SPECIFIC
        return CROSS_UNKNOWN

    if asset_class == ASSET_CLASS_COMMODITY:
        if asset_move >= 2.0:
            return CROSS_COMMODITY_GEOPOLITICAL
        return CROSS_UNKNOWN

    return CROSS_UNKNOWN
