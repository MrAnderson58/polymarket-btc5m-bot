"""Timeline offsets for Market Replay Engine V1."""

from __future__ import annotations

# Minutes relative to ENTRY (0).
REPLAY_OFFSETS_MIN: tuple[int, ...] = (
    -60, -30, -15, -10, -5, -3, -1,
    0,
    1, 3, 5, 10, 15, 30, 60,
)

SNAPSHOT_FIELDS: tuple[str, ...] = (
    "price",
    "volume",
    "oi",
    "funding",
    "funding_delta",
    "fear_greed",
    "btc_dominance",
    "atr",
    "ema20",
    "ema50",
    "ema200",
    "vwap",
    "macd",
    "adx",
    "rsi",
    "stochastic",
    "news_score",
    "ai_score",
    "pattern",
    "optimizer_state",
    "gate_decision",
    "alpha_cluster",
    "edge_cluster",
    "regime",
)

LIQUIDITY_FIELDS: tuple[str, ...] = (
    "orderbook_imbalance",
    "volume_expansion",
    "oi_expansion",
    "liquidation_clusters",
    "volatility_expansion",
)


def offset_label(offset_min: int) -> str:
    if offset_min == 0:
        return "ENTRY"
    if offset_min < 0:
        return f"T{offset_min}m"
    return f"+{offset_min}m"


__all__ = [
    "LIQUIDITY_FIELDS",
    "REPLAY_OFFSETS_MIN",
    "SNAPSHOT_FIELDS",
    "offset_label",
]
