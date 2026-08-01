"""Regime-conditioned edge measurement."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.signal_evolution_v1.rolling import (
    window_metrics,
)

REGIME_KEYS = (
    "BULL",
    "BEAR",
    "RANGE",
    "HIGH_VOL",
    "LOW_VOL",
    "NEWS",
    "MACRO",
)


def _tag_regime(occ: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    regime = str(occ.get("regime") or "").upper()
    if "BULL" in regime or "RISK_ON" in regime or "TREND_UP" in regime:
        tags.append("BULL")
    if "BEAR" in regime or "RISK_OFF" in regime or "TREND_DOWN" in regime:
        tags.append("BEAR")
    if "RANGE" in regime or "SIDE" in regime or regime in ("", "UNKNOWN"):
        tags.append("RANGE")
    atr = occ.get("atr_pct")
    if atr is None:
        atr = occ.get("atr")
    try:
        atr_f = float(atr) if atr is not None else None
    except Exception:
        atr_f = None
    if atr_f is not None:
        tags.append("HIGH_VOL" if atr_f >= 1.5 else "LOW_VOL")
    news = occ.get("news_score")
    try:
        if news is not None and abs(float(news)) >= 0.3:
            tags.append("NEWS")
    except Exception:
        pass
    macro = occ.get("macro_score")
    fear = occ.get("fear_greed")
    try:
        if macro is not None and abs(float(macro)) >= 0.3:
            tags.append("MACRO")
        elif fear is not None and (float(fear) <= 25 or float(fear) >= 75):
            tags.append("MACRO")
    except Exception:
        pass
    if not tags:
        tags.append("RANGE")
    return tags


def regime_adaptation(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {k: [] for k in REGIME_KEYS}
    for occ in occurrences:
        pnl = occ.get("pnl")
        if pnl is None:
            continue
        try:
            p = float(pnl)
        except Exception:
            continue
        for tag in _tag_regime(occ):
            if tag in buckets:
                buckets[tag].append(p)
    return {k: window_metrics(v) for k, v in buckets.items()}


__all__ = ["REGIME_KEYS", "regime_adaptation"]
