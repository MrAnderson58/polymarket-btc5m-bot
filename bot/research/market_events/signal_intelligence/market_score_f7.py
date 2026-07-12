"""Phase F.7 Task A — Global Market Score (0–100)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles

WEIGHTS: dict[str, float] = {
    "btc_trend": 0.15,
    "eth_trend": 0.10,
    "total3": 0.10,
    "btc_dominance": 0.10,
    "funding": 0.10,
    "open_interest": 0.15,
    "liquidations": 0.15,
    "volume_regime": 0.10,
    "volatility_atr": 0.05,
}


@dataclass(frozen=True)
class MarketScoreF7:
    score: float
    components: dict[str, float]


def _clamp_score(v: float) -> float:
    return round(max(0.0, min(100.0, v)), 1)


def _return_to_score(return_pct: float, *, favorable_down: bool = True) -> float:
    """Map return % to 0–100 where extreme moves score higher for reversal setups."""
    abs_ret = abs(return_pct)
    if favorable_down and return_pct < 0:
        return _clamp_score(40 + min(abs_ret, 12) * 5)
    if not favorable_down and return_pct > 0:
        return _clamp_score(40 + min(abs_ret, 12) * 5)
    return _clamp_score(50 - min(abs_ret, 8) * 3)


def _peer_return(conn: Any, symbol: str, bars: int = 12) -> float | None:
    candles = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=bars + 2)
    if len(candles) < 2:
        return None
    start, end = candles[0].close, candles[-1].close
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _funding_score(funding: float | None, regime: str | None) -> float:
    if funding is None:
        return 50.0
    base = 50.0
    if funding < -0.01:
        base += 25
    elif funding < 0:
        base += 15
    elif funding > 0.03:
        base -= 20
    if regime == "flattening":
        base += 10
    elif regime == "accelerating" and funding and funding > 0:
        base -= 10
    return _clamp_score(base)


def _oi_score(regime: str | None, oi_delta: float | None) -> float:
    if oi_delta is not None and oi_delta > 0.5:
        return 75.0
    if regime in ("rising", "stable"):
        return 70.0
    if regime in ("falling", "divergence"):
        return 55.0
    return 50.0


def _volume_score(rvol: float | None) -> float:
    if rvol is None:
        return 50.0
    if rvol >= 3.0:
        return 85.0
    if rvol >= 2.0:
        return 72.0
    if rvol >= 1.5:
        return 62.0
    return 45.0


def _atr_score(atr_mult: float | None) -> float:
    if atr_mult is None:
        return 50.0
    if atr_mult >= 2.5:
        return 80.0
    if atr_mult >= 1.8:
        return 65.0
    return 50.0


def _dominance_component(dominance_regime: str | None) -> float:
    mapping = {
        "ALT SEASON": 75.0,
        "RISK ON": 70.0,
        "CAPITAL INTO BTC": 55.0,
        "BTC ROTATION": 50.0,
        "RISK OFF": 35.0,
    }
    return mapping.get(dominance_regime or "", 50.0)


def _liquidation_component(liq_intel: dict[str, Any] | None) -> float:
    if not liq_intel:
        return 50.0
    regime = str(liq_intel.get("regime") or "")
    if regime in ("long_squeeze", "cascading_long"):
        return 82.0
    if regime in ("short_squeeze", "cascading_short"):
        return 78.0
    if regime == "liquidation_cluster":
        return 70.0
    if liq_intel.get("exhaustion"):
        return 75.0
    return 50.0


def compute_market_score(
    conn: Any,
    *,
    report: Any | None,
    trend: dict[str, Any] | None,
    dominance_regime: str | None,
    liq_intel: dict[str, Any] | None,
) -> MarketScoreF7:
    btc_ret = _peer_return(conn, "BTC")
    eth_ret = _peer_return(conn, "ETH")
    total3_ret = _peer_return(conn, "TOTAL3")

    fund = None
    fund_regime = None
    oi_regime = None
    rvol = None
    atr_mult = None
    if report:
        fund_regime = report.funding_regime
        oi_regime = report.oi_regime
        rvol = report.rvol_20
        atr_mult = report.atr_expansion_multiple
    if trend:
        fund = trend.get("funding")
        if trend.get("atr_multiple"):
            atr_mult = float(trend["atr_multiple"])

    components = {
        "btc_trend": _return_to_score(btc_ret or 0.0) if btc_ret is not None else 50.0,
        "eth_trend": _return_to_score(eth_ret or 0.0) if eth_ret is not None else 50.0,
        "total3": _return_to_score(total3_ret or 0.0) if total3_ret is not None else 50.0,
        "btc_dominance": _dominance_component(dominance_regime),
        "funding": _funding_score(float(fund) if fund is not None else None, fund_regime),
        "open_interest": _oi_score(oi_regime, trend.get("open_interest_delta") if trend else None),
        "liquidations": _liquidation_component(liq_intel),
        "volume_regime": _volume_score(rvol),
        "volatility_atr": _atr_score(atr_mult),
    }

    score = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)
    return MarketScoreF7(score=round(score, 1), components={k: round(v, 1) for k, v in components.items()})


def apply_market_score_multiplier(confidence: float, market_score: float) -> float:
    """Scale confidence by market score: 100 → ×1.0, 0 → ×0.55."""
    multiplier = 0.55 + 0.45 * (max(0.0, min(100.0, market_score)) / 100.0)
    return round(min(10.0, max(0.0, confidence * multiplier)), 1)
