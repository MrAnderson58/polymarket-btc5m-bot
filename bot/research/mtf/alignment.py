"""Time alignment validation for MTF markets — no look-ahead."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.research.mtf.config import SLUG_15M_PREFIX, TF_15M_SECONDS, TF_1H_SECONDS
from bot.research.mtf.discovery import (
    ET,
    discover_15m_market,
    discover_1h_market,
    discover_daily_market,
    parse_15m_window_start_ts,
    slug_1h_at,
    slug_daily_at,
)

WRONG_15M_WINDOW = "WRONG_15M_WINDOW"
WRONG_1H_MARKET = "WRONG_1H_MARKET"
WRONG_DAILY_MARKET = "WRONG_DAILY_MARKET"


def expected_15m_slug(ts: int) -> str | None:
    ref = discover_15m_market(ts)
    return ref.slug if ref else None


def is_15m_window_correct(slug: str | None, snapshot_ts: int) -> bool:
    if not slug:
        return False
    ws = parse_15m_window_start_ts(slug)
    if ws is None:
        return False
    return ws <= snapshot_ts < ws + TF_15M_SECONDS


def expected_1h_slug(ts: int) -> str:
    """Expected active 1h slug at snapshot time (ET hour floor)."""
    dt = datetime.fromtimestamp(ts, tz=ET)
    hour_start = dt.replace(minute=0, second=0, microsecond=0)
    return slug_1h_at(int(hour_start.timestamp()))


def is_1h_market_correct(slug: str | None, snapshot_ts: int) -> bool:
    if not slug:
        return False
    expected = discover_1h_market(snapshot_ts)
    if expected is None:
        return False
    return slug == expected.slug


def expected_daily_slug(ts: int) -> str | None:
    ref = discover_daily_market(ts)
    return ref.slug if ref else None


def is_daily_market_correct(slug: str | None, snapshot_ts: int) -> bool:
    if not slug:
        return False
    expected = discover_daily_market(snapshot_ts)
    if expected is None:
        return False
    return slug == expected.slug


def parse_15m_slug_bounds(slug: str) -> tuple[int, int] | None:
    ws = parse_15m_window_start_ts(slug)
    if ws is None:
        return None
    return ws, ws + TF_15M_SECONDS


def active_interval_label(slug: str | None, timeframe: str) -> str:
    if not slug:
        return "NONE"
    if timeframe == "15m":
        bounds = parse_15m_slug_bounds(slug)
        if not bounds:
            return "PARSE_FAILED"
        ws, we = bounds
        return f"{ws} .. {we} UTC"
    if timeframe == "1h":
        m = re.match(
            r"^bitcoin-up-or-down-([a-z]+)-(\d+)-(\d+)-(\d{1,2}(?:am|pm))-et$",
            slug,
        )
        if not m:
            return "PARSE_FAILED"
        from bot.research.mtf.strike_resolver import _MONTHS, _parse_hour_label

        month_name, day, year, hour_label = m.groups()
        month = _MONTHS.get(month_name.lower())
        hour = _parse_hour_label(hour_label)
        if not month or hour is None:
            return "PARSE_FAILED"
        ws_dt = datetime(int(year), month, int(day), hour, 0, tzinfo=ET)
        we_dt = ws_dt + timedelta(seconds=TF_1H_SECONDS)
        return f"{int(ws_dt.timestamp())} .. {int(we_dt.timestamp())} ET hour"
    if timeframe == "daily":
        return f"daily slug {slug} (resolves noon ET comparison)"
    return "UNKNOWN"
