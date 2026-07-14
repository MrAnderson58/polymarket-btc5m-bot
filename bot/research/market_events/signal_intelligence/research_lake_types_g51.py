"""Phase G.5.1 — Research Data Lake window definitions and helpers."""

from __future__ import annotations

G51_WINDOWS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "12h": 43200,
    "24h": 86400,
}

G51_REPLAY_WINDOWS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "24h": 86400,
}

G51_LIQUIDITY_METRICS = ("funding", "oi", "liquidations", "volume")

G51_COMPLETENESS_KEYS = (
    "funding", "oi", "replay", "candles", "false_rejects",
)

MISSING_INFO_ALIASES: dict[str, tuple[str, ...]] = {
    "funding": ("funding history", "funding rate history", "funding", "funding rate"),
    "oi": ("oi history", "oi change history", "open interest", "open interest history", "oi"),
    "candles": ("5m candles", "candle history", "candles", "5m candle"),
    "replay": ("replay outcomes", "replay", "completed replay", "replay-complete"),
    "liquidations": ("liquidation history", "liquidation cluster history", "liquidations"),
    "false_rejects": ("false reject", "false rejects", "false reject analysis"),
}
