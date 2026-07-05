"""Time alignment validation for MTF markets — no look-ahead, no HTTP."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.research.mtf.config import SLUG_15M_PREFIX, TF_15M_SECONDS, TF_1H_SECONDS
from bot.research.mtf.discovery import parse_15m_window_start_ts, slug_1h_at, slug_daily_at
from bot.research.mtf.window_bounds import (
    parse_1h_window_bounds,
    parse_daily_window_bounds,
    snapshot_in_window,
)

ET = ZoneInfo("America/New_York")

WRONG_15M_WINDOW = "WRONG_15M_WINDOW"
WRONG_1H_MARKET = "WRONG_1H_MARKET"
WRONG_DAILY_MARKET = "WRONG_DAILY_MARKET"


def expected_15m_slug(ts: int) -> str:
    aligned = (ts // TF_15M_SECONDS) * TF_15M_SECONDS
    return f"{SLUG_15M_PREFIX}-{aligned}"


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
    return snapshot_in_window(slug, snapshot_ts, parse_1h_window_bounds)


def expected_daily_slug(ts: int) -> str:
    return slug_daily_at(ts)


def is_daily_market_correct(slug: str | None, snapshot_ts: int) -> bool:
    if not slug:
        return False
    return snapshot_in_window(slug, snapshot_ts, parse_daily_window_bounds)


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
        bounds = parse_1h_window_bounds(slug)
        if not bounds:
            return "PARSE_FAILED"
        ws, we = bounds
        return f"{ws} .. {we} ET hour"
    if timeframe == "daily":
        bounds = parse_daily_window_bounds(slug)
        if bounds:
            ws, we = bounds
            return f"{ws} .. {we} ET daily window"
        return f"daily slug {slug} (parse failed)"
    return "UNKNOWN"
