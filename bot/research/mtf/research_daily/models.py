"""Daily observation model and window parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


@dataclass
class ObsDaily:
    timestamp: int
    market_slug: str
    window_start_ts: int
    window_end_ts: int
    entry_second: int
    seconds_left: int | None
    btc_price: float | None
    strike: float | None
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    spread: float | None
    yes_mid: float | None
    session: str = "unknown"
    dist_strike_usd: float = 0.0
    dist_strike_pct: float = 0.0
    dist_daily_open_usd: float = 0.0
    dist_daily_open_pct: float = 0.0
    dist_from_high_pct: float = 0.0
    dist_from_low_pct: float = 0.0
    realized_vol: float = 0.0
    vol_regime: str = "normal"
    intraday_trend_usd: float = 0.0
    btc_trend_1h: float | None = None
    btc_trend_4h: float | None = None
    pm_prob_1h: float | None = None


@dataclass
class SimTradeDaily:
    family: str
    side: str
    market_slug: str
    entry_ts: int
    entry_price: float
    entry_second: int
    exit_ts: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl_pct: float | None = None
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    session: str = "unknown"


def parse_daily_window(slug: str) -> tuple[int, int] | None:
    m = re.match(r"^bitcoin-up-or-down-on-([a-z]+)-(\d+)-(\d+)$", slug)
    if not m:
        return None
    month_name, day, year = m.groups()
    month = _MONTHS.get(month_name.lower())
    if not month:
        return None
    end_dt = datetime(int(year), month, int(day), 12, 0, tzinfo=ET)
    start_dt = end_dt - timedelta(days=1)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def session_label(entry_second: int) -> str:
    """Approximate ET session from seconds since daily window open (prior noon)."""
    hour_et = (entry_second % 86400) / 3600
    if 0 <= hour_et < 8:
        return "asia"
    if 8 <= hour_et < 14:
        return "europe"
    return "us"
