"""Extract categorical feature tags for Elite Market Profile V1."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    normalize_coin,
    safe_float,
    session_from_hour,
)

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _hour_weekday(opened_at: Any) -> tuple[int | None, str | None]:
    try:
        ts = int(opened_at)
    except Exception:
        return None, None
    if ts <= 0:
        return None, None
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.hour, WEEKDAYS[dt.weekday()]


def _bucket_atr(atr: float | None, atr_pct: float | None) -> str | None:
    v = atr_pct if atr_pct is not None else atr
    if v is None:
        return None
    # atr_pct often 0..few %; raw atr may be absolute
    x = float(v)
    if x > 5:  # likely absolute or percent*100 — normalize soft
        x = x / 100.0 if x > 50 else x
    if x < 0.25:
        return "ATR<0.25"
    if x < 0.5:
        return "ATR<0.50"
    return "ATR>=0.50"


def _sign_tag(name: str, v: float | None, *, eps: float = 1e-9) -> str | None:
    if v is None:
        return None
    if v > eps:
        return f"{name}+"
    if v < -eps:
        return f"{name}-"
    return f"{name}0"


def _rsi_tag(rsi: float | None) -> str | None:
    if rsi is None:
        return None
    if rsi < 30:
        return "RSI_oversold"
    if rsi > 70:
        return "RSI_overbought"
    return "RSI_mid"


def _fear_tag(fg: float | None) -> str | None:
    if fg is None:
        return None
    if fg >= 60:
        return "Fear_greed_high"
    if fg <= 30:
        return "Fear_extreme"
    return "Fear_neutral"


def _adx_tag(adx: float | None) -> str | None:
    if adx is None:
        return None
    if adx >= 25:
        return "ADX_strong"
    return "ADX_weak"


def _ema_tag(dist: float | None) -> str | None:
    if dist is None:
        return None
    return "EMA_above" if dist >= 0 else "EMA_below"


def _vwap_tag(dist: float | None) -> str | None:
    if dist is None:
        return None
    return "VWAP_above" if dist >= 0 else "VWAP_below"


def _macd_tag(macd: float | None, hist: float | None) -> str | None:
    v = hist if hist is not None else macd
    if v is None:
        return None
    return "MACD<0" if v < 0 else "MACD>0"


def _fp_tag(sim: float | None) -> str | None:
    if sim is None:
        return None
    if sim >= 0.7:
        return "Fingerprint_high"
    if sim >= 0.45:
        return "Fingerprint_mid"
    return "Fingerprint_low"


def _tl_tag(sim: float | None) -> str | None:
    if sim is None:
        return None
    if sim >= 0.7:
        return "Timeline_high"
    if sim >= 0.45:
        return "Timeline_mid"
    return "Timeline_low"


def extract_tags(
    row: dict[str, Any],
    *,
    lake: dict[str, Any] | None = None,
) -> list[str]:
    """
    Build categorical tags for one trade.
    Prefer lake features; fall back to elite/journal row fields.
    """
    src = dict(lake or {})
    src.update({k: v for k, v in row.items() if v is not None})

    tags: list[str] = []
    coin = normalize_coin(src.get("symbol") or row.get("symbol"))
    if coin:
        tags.append(f"COIN={coin}")

    direction = str(src.get("direction") or row.get("direction") or "").upper()
    if direction in ("LONG", "SHORT", "UP", "DOWN", "BUY", "SELL"):
        if direction in ("UP", "BUY"):
            direction = "LONG"
        elif direction in ("DOWN", "SELL"):
            direction = "SHORT"
        tags.append(direction)

    hour, weekday = _hour_weekday(src.get("opened_at") or row.get("opened_at"))
    if hour is not None:
        tags.append(f"HOUR={hour:02d}")
        sess = session_from_hour(hour)
        if sess:
            # User-facing NY label
            if sess == "NewYork":
                sess = "NY"
            tags.append(f"SESSION={sess}")
    if weekday:
        tags.append(f"WEEKDAY={weekday}")

    atr = safe_float(src.get("atr_pct") if src.get("atr_pct") is not None else src.get("atr"))
    atr_tag = _bucket_atr(safe_float(src.get("atr")), safe_float(src.get("atr_pct")))
    if atr_tag:
        tags.append(atr_tag)

    for t in (
        _adx_tag(safe_float(src.get("adx"))),
        _ema_tag(safe_float(src.get("ema20_distance"))),
        _vwap_tag(safe_float(src.get("vwap_distance"))),
        _macd_tag(safe_float(src.get("macd")), safe_float(src.get("macd_hist"))),
        _rsi_tag(safe_float(src.get("rsi"))),
        _sign_tag("Funding", safe_float(src.get("funding"))),
        _sign_tag("OI", safe_float(src.get("oi_delta") if src.get("oi_delta") is not None else src.get("open_interest"))),
        _fear_tag(safe_float(src.get("fear_greed"))),
        _fp_tag(safe_float(row.get("current_fingerprint") or row.get("fingerprint_similarity") or src.get("fingerprint_similarity"))),
        _tl_tag(safe_float(row.get("timeline_similarity") or src.get("timeline_similarity"))),
    ):
        if t:
            tags.append(t)

    regime = None
    if lake:
        regime = lake.get("regime") or lake.get("market_regime")
    if not regime:
        regime = src.get("regime") or src.get("market_regime")
    # Avoid elite-only transition engine labels dominating vs IGNORE
    if not regime:
        regime = row.get("current_regime") if lake is None else None
    if regime:
        tags.append(f"Regime={str(regime).upper()}")

    transition = None
    if lake:
        transition = lake.get("current_transition") or lake.get("transition")
    if not transition and lake is None:
        transition = row.get("current_transition")
    if transition:
        tags.append(f"Transition={transition}")

    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


__all__ = ["WEEKDAYS", "extract_tags"]
