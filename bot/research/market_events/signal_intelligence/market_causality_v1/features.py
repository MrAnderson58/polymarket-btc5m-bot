"""Causal feature set and extraction for Market Causality Engine V1."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

# Attribution dimensions (normalized to 100%).
CAUSE_FEATURES: tuple[str, ...] = (
    "rsi",
    "ema",
    "macd",
    "adx",
    "atr",
    "volume",
    "oi",
    "funding",
    "fear",
    "news",
    "ai",
    "pattern",
    "regime",
    "time_of_day",
    "symbol",
)

# Directed template edges (parent -> child) for the causal graph skeleton.
CAUSAL_TEMPLATE_EDGES: tuple[tuple[str, str], ...] = (
    ("funding", "oi"),
    ("oi", "volume"),
    ("volume", "atr"),
    ("atr", "breakout"),
    ("fear", "volume"),
    ("news", "ai"),
    ("ai", "breakout"),
    ("rsi", "mean_reversion"),
    ("ema", "trend"),
    ("macd", "trend"),
    ("adx", "trend"),
    ("pattern", "breakout"),
    ("regime", "breakout"),
    ("time_of_day", "volume"),
    ("symbol", "regime"),
    ("breakout", "outcome"),
    ("trend", "outcome"),
    ("mean_reversion", "outcome"),
)


def _hour_from_ts(ts: int | None) -> float | None:
    if ts is None:
        return None
    try:
        import datetime as _dt
        return float(_dt.datetime.utcfromtimestamp(int(ts)).hour)
    except Exception:
        return None


def extract_cause_vector(trade: dict[str, Any]) -> dict[str, float]:
    """Map lake/replay trade fields onto CAUSE_FEATURES (signed magnitudes)."""
    rsi = _safe_float(trade.get("rsi"))
    # Extreme RSI → larger causal energy
    rsi_e = 0.0 if rsi is None else abs(float(rsi) - 50.0) / 50.0

    ema20 = _safe_float(trade.get("ema20_distance") or trade.get("ema20"))
    ema50 = _safe_float(trade.get("ema50_distance") or trade.get("ema50"))
    ema = 0.0
    for v in (ema20, ema50):
        if v is not None:
            ema = max(ema, abs(float(v)))

    macd = abs(_safe_float(trade.get("macd")) or 0.0)
    adx = abs(_safe_float(trade.get("adx")) or 0.0) / 50.0
    atr = abs(_safe_float(trade.get("atr_pct") or trade.get("atr")) or 0.0)
    volume = abs(_safe_float(trade.get("volume")) or 0.0)
    oi = abs(_safe_float(trade.get("oi_delta") or trade.get("oi") or trade.get("open_interest")) or 0.0)
    funding = abs(_safe_float(trade.get("funding") or trade.get("funding_delta")) or 0.0) * 100.0
    fear = abs((_safe_float(trade.get("fear_greed")) or 50.0) - 50.0) / 50.0
    news = abs(_safe_float(trade.get("news_score")) or 0.0)
    ai = abs(_safe_float(trade.get("ai_score")) or 0.0)
    pattern = 1.0 if str(trade.get("pattern") or "").strip() else 0.0
    regime = 1.0 if str(trade.get("regime") or trade.get("market_regime") or "").strip() else 0.0
    hour = _safe_float(trade.get("hour"))
    if hour is None:
        hour = _hour_from_ts(trade.get("entry_ts") or trade.get("opened_at") or trade.get("closed_at"))
    # Time-of-day energy: distance from mid-day
    tod = 0.0 if hour is None else abs(float(hour) - 12.0) / 12.0
    symbol = 1.0 if str(trade.get("symbol") or "").strip() else 0.0

    return {
        "rsi": float(rsi_e),
        "ema": float(ema),
        "macd": float(macd),
        "adx": float(min(adx, 3.0)),
        "atr": float(atr),
        "volume": float(np.log1p(volume)),
        "oi": float(np.log1p(oi) if oi > 1 else oi),
        "funding": float(funding),
        "fear": float(fear),
        "news": float(news),
        "ai": float(ai),
        "pattern": float(pattern),
        "regime": float(regime),
        "time_of_day": float(tod),
        "symbol": float(symbol),
    }


def pre_entry_deltas_from_frames(frames: list[dict[str, Any]]) -> dict[str, float]:
    """Only use frames with offset_min < 0 (reject future leakage)."""
    pre = [f for f in frames if int(f.get("offset_min") or 0) < 0]
    if len(pre) < 2:
        return {}
    first, last = pre[0], pre[-1]

    def delta(key: str, scale: float = 1.0) -> float:
        a = _safe_float(first.get(key))
        b = _safe_float(last.get(key))
        if a is None or b is None:
            return 0.0
        return abs(float(b) - float(a)) * scale

    return {
        "funding": delta("funding", 1000.0),
        "oi": delta("oi", 1e-6) if _safe_float(first.get("oi")) and abs(_safe_float(first.get("oi")) or 0) > 1e3 else delta("oi"),
        "volume": delta("volume", 0.01),
        "atr": delta("atr"),
        "fear": delta("fear_greed", 0.02),
        "rsi": delta("rsi", 0.02),
        "macd": delta("macd"),
        "adx": delta("adx", 0.02),
        "ema": delta("ema20", 0.01),
    }


def merge_cause_signal(
    base: dict[str, float],
    deltas: dict[str, float],
) -> dict[str, float]:
    """Blend static lake features with pre-entry deltas (temporal causes only)."""
    out = dict(base)
    for k, v in deltas.items():
        if k in out:
            out[k] = float(out[k]) * 0.5 + float(v) * 0.5
        else:
            out[k] = float(v)
    # Ensure all keys present
    for k in CAUSE_FEATURES:
        out.setdefault(k, 0.0)
    return out


__all__ = [
    "CAUSE_FEATURES",
    "CAUSAL_TEMPLATE_EDGES",
    "extract_cause_vector",
    "merge_cause_signal",
    "pre_entry_deltas_from_frames",
]
