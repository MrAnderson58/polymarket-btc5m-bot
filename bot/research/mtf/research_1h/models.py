"""1h observation model and window parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.research.mtf.config import TF_1H_SECONDS

ET = ZoneInfo("America/New_York")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


@dataclass
class Obs1h:
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
    btc_move_1m: float = 0.0
    btc_move_3m: float = 0.0
    btc_move_5m: float = 0.0
    btc_move_10m: float = 0.0
    btc_move_15m: float = 0.0
    dist_strike_usd: float = 0.0
    dist_strike_pct: float = 0.0
    btc_trend_5m: float | None = None
    btc_trend_15m: float | None = None
    btc_trend_30m: float | None = None
    btc_trend_1h: float | None = None
    realized_vol: float = 0.0
    vol_expansion: float = 1.0
    momentum_consistency: float = 0.0
    prob_change_5m: float = 0.0
    pm_prob_15m: float | None = None


@dataclass
class SimTrade1h:
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
    regime: str = "unknown"


def _parse_hour(label: str) -> int | None:
    m = re.match(r"^(\d{1,2})(am|pm)$", label.lower())
    if not m:
        return None
    h = int(m.group(1))
    return 0 if h == 12 and m.group(2) == "am" else (12 if h == 12 else h if m.group(2) == "am" else h + 12)


def parse_1h_window(slug: str) -> tuple[int, int] | None:
    m = re.match(r"^bitcoin-up-or-down-([a-z]+)-(\d+)-(\d+)-(\d{1,2}(?:am|pm))-et$", slug)
    if not m:
        return None
    month_name, day, year, hour_label = m.groups()
    month = _MONTHS.get(month_name.lower())
    hour = _parse_hour(hour_label)
    if not month or hour is None:
        return None
    ws = int(datetime(int(year), month, int(day), hour, 0, tzinfo=ET).timestamp())
    return ws, ws + TF_1H_SECONDS
