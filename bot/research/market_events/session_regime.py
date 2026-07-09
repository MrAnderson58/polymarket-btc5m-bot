"""US equity and commodity session regime classification."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.research.market_events.instrument_types import (
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_ETF,
    ASSET_CLASS_INDEX,
    HOURS_CRYPTO_24H,
    HOURS_US_EQUITY,
    SESSION_AFTER_HOURS,
    SESSION_CRYPTO_24H,
    SESSION_PREMARKET,
    SESSION_UNDERLYING_CLOSED,
    SESSION_US_REGULAR,
    SESSION_WEEKEND,
)

ET = ZoneInfo("America/New_York")


def classify_session_regime(
    ts: int,
    *,
    asset_class: str,
    trading_hours_mode: str,
) -> str:
    if asset_class == ASSET_CLASS_CRYPTO or trading_hours_mode == HOURS_CRYPTO_24H:
        return SESSION_CRYPTO_24H

    if asset_class not in (ASSET_CLASS_EQUITY, ASSET_CLASS_ETF, ASSET_CLASS_INDEX, ASSET_CLASS_COMMODITY):
        return SESSION_UNDERLYING_CLOSED

    dt = datetime.fromtimestamp(ts, tz=ET)
    if dt.weekday() >= 5:
        return SESSION_WEEKEND

    # US equity session windows (ET)
    minutes = dt.hour * 60 + dt.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return SESSION_PREMARKET
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return SESSION_US_REGULAR
    if 16 * 60 <= minutes < 20 * 60:
        return SESSION_AFTER_HOURS
    return SESSION_UNDERLYING_CLOSED
