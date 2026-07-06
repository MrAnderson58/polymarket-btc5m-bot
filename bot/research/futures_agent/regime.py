"""Deterministic BTC regime, alignment, and preliminary research labels."""

from __future__ import annotations

from bot.research.futures_agent.config import (
    ALIGNMENT_ALIGNED,
    ALIGNMENT_ALT_RS,
    ALIGNMENT_ALT_RW,
    ALIGNMENT_COUNTERTREND,
    ALIGNMENT_NEUTRAL,
    BTC_REGIME_DOWN,
    BTC_REGIME_MIXED,
    BTC_REGIME_STRONG_DOWN,
    BTC_REGIME_STRONG_UP,
    BTC_REGIME_UP,
    RESEARCH_COUNTERTREND,
    RESEARCH_HIGH_RISK,
    RESEARCH_INSUFFICIENT,
    RESEARCH_MIXED,
    RESEARCH_SUPPORTIVE,
    VOL_REGIME_HIGH,
    VOL_REGIME_LOW,
    VOL_REGIME_NORMAL,
)
from bot.research.futures_agent.features import AssetSnapshot, trend_label


def btc_market_regime(snap: AssetSnapshot) -> str:
    r1h = snap.return_1h
    r4h = snap.return_4h
    if r1h is None and r4h is None:
        return BTC_REGIME_MIXED
    score = 0.0
    if r1h is not None:
        score += r1h
    if r4h is not None:
        score += r4h * 0.5
    if score > 1.0:
        return BTC_REGIME_STRONG_UP
    if score > 0.25:
        return BTC_REGIME_UP
    if score < -1.0:
        return BTC_REGIME_STRONG_DOWN
    if score < -0.25:
        return BTC_REGIME_DOWN
    return BTC_REGIME_MIXED


def volatility_regime(snap: AssetSnapshot) -> str:
    rv = snap.realized_vol
    if rv is None:
        return VOL_REGIME_NORMAL
    if rv < 0.05:
        return VOL_REGIME_LOW
    if rv > 0.25:
        return VOL_REGIME_HIGH
    return VOL_REGIME_NORMAL


def momentum_regime(snap: AssetSnapshot) -> str:
    r5 = snap.return_5m
    r15 = snap.return_15m
    if r5 is None and r15 is None:
        return "UNKNOWN"
    score = (r5 or 0.0) + (r15 or 0.0) * 0.5
    if score > 0.3:
        return "POSITIVE"
    if score < -0.3:
        return "NEGATIVE"
    return "NEUTRAL"


def alignment_label(
    *,
    signal_direction: str | None,
    alt_snap: AssetSnapshot,
    btc_snap: AssetSnapshot,
    excess_1h: float | None,
) -> str:
    if excess_1h is not None:
        if excess_1h > 0.3:
            return ALIGNMENT_ALT_RS
        if excess_1h < -0.3:
            return ALIGNMENT_ALT_RW

    btc_reg = btc_market_regime(btc_snap)
    if btc_reg == BTC_REGIME_MIXED:
        return ALIGNMENT_NEUTRAL

    side = (signal_direction or "").upper()
    btc_bull = btc_reg in (BTC_REGIME_UP, BTC_REGIME_STRONG_UP)
    btc_bear = btc_reg in (BTC_REGIME_DOWN, BTC_REGIME_STRONG_DOWN)

    if side == "LONG" and btc_bull:
        return ALIGNMENT_ALIGNED
    if side == "SHORT" and btc_bear:
        return ALIGNMENT_ALIGNED
    if side == "LONG" and btc_bear:
        return ALIGNMENT_COUNTERTREND
    if side == "SHORT" and btc_bull:
        return ALIGNMENT_COUNTERTREND
    return ALIGNMENT_NEUTRAL


def relative_strength_label(excess_5m: float | None, excess_1h: float | None) -> str:
    if excess_1h is not None and excess_1h > 0.5:
        return "OUTPERFORMING"
    if excess_1h is not None and excess_1h < -0.5:
        return "UNDERPERFORMING"
    if excess_5m is not None and excess_5m > 0.2:
        return "SHORT_TERM_OUTPERFORM"
    if excess_5m is not None and excess_5m < -0.2:
        return "SHORT_TERM_UNDERPERFORM"
    return "INLINE"


def preliminary_research_label(
    *,
    data_quality: str,
    alignment: str,
    btc_regime: str,
    vol_regime: str,
    passes_gate: bool,
) -> str:
    from bot.research.futures_agent.config import DATA_QUALITY_COMPLETE, DATA_QUALITY_PARTIAL

    if data_quality not in (DATA_QUALITY_COMPLETE, DATA_QUALITY_PARTIAL):
        return RESEARCH_INSUFFICIENT
    if not passes_gate:
        return RESEARCH_INSUFFICIENT
    if vol_regime == VOL_REGIME_HIGH:
        return RESEARCH_HIGH_RISK
    if alignment == ALIGNMENT_COUNTERTREND:
        return RESEARCH_COUNTERTREND
    if alignment in (ALIGNMENT_ALIGNED, ALIGNMENT_ALT_RS) and btc_regime in (
        BTC_REGIME_UP, BTC_REGIME_STRONG_UP, BTC_REGIME_DOWN, BTC_REGIME_STRONG_DOWN,
    ):
        return RESEARCH_SUPPORTIVE
    return RESEARCH_MIXED


def btc_trend_fields(snap: AssetSnapshot) -> dict[str, str | None]:
    return {
        "trend_15m": trend_label(snap.return_15m),
        "trend_1h": trend_label(snap.return_1h),
        "trend_4h": trend_label(snap.return_4h),
    }
