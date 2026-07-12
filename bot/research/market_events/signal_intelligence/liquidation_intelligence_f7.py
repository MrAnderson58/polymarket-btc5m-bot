"""Phase F.7 Task B — multi-exchange liquidation intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import CandleBar, load_recent_candles

REGIME_LONG_SQUEEZE = "long_squeeze"
REGIME_SHORT_SQUEEZE = "short_squeeze"
REGIME_CASCADING = "cascading_liquidations"
REGIME_CLUSTER = "liquidation_cluster"
REGIME_NEUTRAL = "neutral"

EXCHANGES = ("bybit", "binance", "hyperliquid")


@dataclass(frozen=True)
class LiquidationIntelF7:
    regime: str
    continuation_probability: float
    reversal_probability: float
    exchanges: dict[str, dict[str, Any]]
    exhaustion: bool
    summary: str


def _volume_spike(bars: list[CandleBar]) -> float:
    if len(bars) < 10:
        return 1.0
    recent = [b.volume for b in bars[-3:]]
    baseline = [b.volume for b in bars[-20:-3]] or [1.0]
    avg_recent = sum(recent) / len(recent)
    avg_base = sum(baseline) / len(baseline)
    if avg_base <= 0:
        return 1.0
    return avg_recent / avg_base


def _exchange_proxy(symbol: str, venue: str, bars: list[CandleBar]) -> dict[str, Any]:
    if len(bars) < 5:
        return {"venue": venue, "intensity": 0.0, "direction": "neutral"}
    last = bars[-1]
    move = (last.close / bars[-5].close - 1.0) * 100.0 if bars[-5].close > 0 else 0.0
    vol_spike = _volume_spike(bars)
    wick_ratio = 0.0
    if last.high > last.low:
        body = abs(last.close - last.open)
        wick = (last.high - last.low) - body
        wick_ratio = wick / (last.high - last.low)
    intensity = min(1.0, vol_spike / 4.0 + abs(move) / 8.0 + wick_ratio * 0.5)
    direction = "long_liquidation" if move < -1.5 and vol_spike > 2 else (
        "short_liquidation" if move > 1.5 and vol_spike > 2 else "neutral"
    )
    return {
        "venue": venue,
        "intensity": round(intensity, 3),
        "direction": direction,
        "move_pct": round(move, 2),
        "volume_spike": round(vol_spike, 2),
    }


def _fetch_exchange_bars(conn: Any, symbol: str, venue: str) -> list[CandleBar]:
    key = "binance_futures" if venue == "binance" else "bybit_linear" if venue == "bybit" else "binance_futures"
    return load_recent_candles(conn, symbol=symbol, venue=key, timeframe="5m", limit=24)


def analyze_liquidations(
    conn: Any,
    *,
    symbol: str,
    trend: dict[str, Any] | None,
    shock_return: float,
) -> LiquidationIntelF7:
    exchanges: dict[str, dict[str, Any]] = {}
    for ex in EXCHANGES:
        bars = _fetch_exchange_bars(conn, symbol, ex)
        exchanges[ex] = _exchange_proxy(symbol, ex, bars)

    long_signals = sum(1 for e in exchanges.values() if e["direction"] == "long_liquidation")
    short_signals = sum(1 for e in exchanges.values() if e["direction"] == "short_liquidation")
    avg_intensity = sum(e["intensity"] for e in exchanges.values()) / max(len(exchanges), 1)
    stage = str(trend.get("stage") or "") if trend else ""
    exhaustion = stage == "Capitulation" or abs(shock_return) >= 5.0

    if long_signals >= 2 and avg_intensity >= 0.5:
        regime = REGIME_CASCADING if avg_intensity >= 0.7 else REGIME_LONG_SQUEEZE
    elif short_signals >= 2 and avg_intensity >= 0.5:
        regime = REGIME_CASCADING if avg_intensity >= 0.7 else REGIME_SHORT_SQUEEZE
    elif avg_intensity >= 0.55:
        regime = REGIME_CLUSTER
    else:
        regime = REGIME_NEUTRAL

    if regime in (REGIME_LONG_SQUEEZE, REGIME_CASCADING) and shock_return < 0:
        reversal_prob = min(0.85, 0.55 + avg_intensity * 0.3)
        cont_prob = 1.0 - reversal_prob
        summary = "Long squeeze / cascading liquidations — откат вероятнее"
    elif regime == REGIME_SHORT_SQUEEZE and shock_return > 0:
        reversal_prob = min(0.80, 0.50 + avg_intensity * 0.3)
        cont_prob = 1.0 - reversal_prob
        summary = "Short squeeze — откат вероятнее"
    elif exhaustion:
        reversal_prob = 0.72
        cont_prob = 0.28
        summary = "Ликвидации заканчиваются — exhaustion"
    else:
        cont_prob = 0.58
        reversal_prob = 0.42
        summary = "Ликвидации нейтральны"

    return LiquidationIntelF7(
        regime=regime,
        continuation_probability=round(cont_prob, 3),
        reversal_probability=round(reversal_prob, 3),
        exchanges=exchanges,
        exhaustion=exhaustion,
        summary=summary,
    )
