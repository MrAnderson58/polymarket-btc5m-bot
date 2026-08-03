"""Discrete state labels along a trade formation timeline."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.market_timeline_v1.windows import (
    CHAIN_WINDOWS,
)


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x or abs(x) == float("inf"):
            return None
        return x
    except Exception:
        return None


def label_window(state: dict[str, Any], *, prior: dict[str, Any] | None = None) -> str:
    """Map one checkpoint to a human-readable formation tag."""
    ret = _f(state.get("ret"))
    slope = _f(state.get("slope"))
    atr = _f(state.get("atr_pct"))
    bb = _f(state.get("bb_width"))
    macd_h = _f(state.get("macd_hist"))
    rsi = _f(state.get("rsi"))
    oi_d = _f(state.get("oi_delta"))
    fund_d = _f(state.get("funding_delta"))
    rvol = _f(state.get("relative_volume") if state.get("relative_volume") is not None else state.get("rvol"))

    prior_atr = _f(prior.get("atr_pct")) if prior else None
    prior_bb = _f(prior.get("bb_width")) if prior else None
    prior_macd = _f(prior.get("macd_hist")) if prior else None
    prior_slope = _f(prior.get("slope")) if prior else None

    # Transition-priority tags (formation dynamics)
    if prior_atr is not None and atr is not None and atr < prior_atr * 0.85:
        return "ATR_compression"
    if prior_atr is not None and atr is not None and atr > prior_atr * 1.20:
        return "ATR_expansion"
    if prior_bb is not None and bb is not None and bb < prior_bb * 0.85:
        return "BB_squeeze"
    if prior_slope is not None and slope is not None:
        if abs(prior_slope) > 1e-9 and abs(slope) > abs(prior_slope) * 1.25:
            return "ADX_rising"  # slope magnitude proxy for trend strength rise
        if abs(prior_slope) > 1e-9 and abs(slope) < abs(prior_slope) * 0.70:
            return "trend_fade"
    if prior_macd is not None and macd_h is not None:
        if prior_macd <= 0 < macd_h:
            return "MACD_cross_up"
        if prior_macd >= 0 > macd_h:
            return "MACD_cross_down"

    if oi_d is not None and oi_d < -0.5:
        return "OI_falling"
    if oi_d is not None and oi_d > 0.5:
        return "OI_rising"
    if fund_d is not None and abs(fund_d) > 1e-5:
        return "funding_squeeze" if fund_d > 0 else "funding_flush"
    if rvol is not None and rvol >= 1.8:
        return "volume_spike"

    # Level tags
    if macd_h is not None and macd_h < 0 and (ret is None or ret <= 0):
        return "MACD_negative"
    if macd_h is not None and macd_h > 0 and (ret is None or ret >= 0):
        return "MACD_positive"
    if rsi is not None and rsi >= 70:
        return "overbought"
    if rsi is not None and rsi <= 30:
        return "oversold"

    if ret is not None:
        if ret <= -1.5:
            return "Bear"
        if ret >= 1.5:
            return "Bull"
        if abs(ret) < 0.45:
            return "Range"
        return "consolidation" if abs(ret) < 1.0 else ("Bear" if ret < 0 else "Bull")

    if slope is not None:
        if slope < -1e-6:
            return "Bear"
        if slope > 1e-6:
            return "Bull"
    return "Range"


def label_chain(row: dict[str, Any]) -> list[str]:
    """Ordered labels for CHAIN_WINDOWS including terminal direction."""
    windows = row.get("windows") or {}
    labels: list[str] = []
    prior = None
    for name in CHAIN_WINDOWS:
        st = windows.get(name)
        if not st:
            labels.append("missing")
            continue
        lab = label_window(st, prior=prior)
        labels.append(lab)
        prior = st
    direction = str(row.get("direction") or "UNK").upper() or "UNK"
    labels.append(direction)
    return labels


def chain_key(labels: list[str]) -> str:
    return " → ".join(labels)


def chain_duration_hint(labels: list[str]) -> str:
    """Structural mid-horizon path used by the fingerprint chain."""
    _ = labels
    # Matches the formation core called out in the engine brief.
    return " → ".join(("4h", "1h", "15m"))


__all__ = ["chain_duration_hint", "chain_key", "label_chain", "label_window"]
