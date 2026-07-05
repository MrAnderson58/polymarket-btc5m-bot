"""Local MTF window bounds from slugs — no network, no look-ahead."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.research.mtf.config import SLUG_15M_PREFIX, TF_15M_SECONDS, TF_1H_SECONDS

ET = ZoneInfo("America/New_York")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_hour_label(label: str) -> int | None:
    m = re.match(r"^(\d{1,2})(am|pm)$", label.lower())
    if not m:
        return None
    h = int(m.group(1))
    if h == 12 and m.group(2) == "am":
        return 0
    if h == 12:
        return 12
    return h if m.group(2) == "am" else h + 12


def parse_15m_window_bounds(slug: str) -> tuple[int, int] | None:
    m = re.match(rf"^{re.escape(SLUG_15M_PREFIX)}-(\d+)$", slug)
    if not m:
        return None
    try:
        ws = int(m.group(1))
    except ValueError:
        return None
    return ws, ws + TF_15M_SECONDS


def parse_1h_window_bounds(slug: str) -> tuple[int, int] | None:
    m = re.match(r"^bitcoin-up-or-down-([a-z]+)-(\d+)-(\d+)-(\d{1,2}(?:am|pm))-et$", slug)
    if not m:
        return None
    month_name, day, year, hour_label = m.groups()
    month = _MONTHS.get(month_name.lower())
    hour = _parse_hour_label(hour_label)
    if not month or hour is None:
        return None
    ws = int(datetime(int(year), month, int(day), hour, 0, tzinfo=ET).timestamp())
    return ws, ws + TF_1H_SECONDS


def parse_daily_window_bounds(slug: str) -> tuple[int, int] | None:
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


def snapshot_in_window(slug: str, snapshot_ts: int, bounds_fn) -> bool:
    bounds = bounds_fn(slug)
    if bounds is None:
        return False
    ws, we = bounds
    return ws <= snapshot_ts < we
