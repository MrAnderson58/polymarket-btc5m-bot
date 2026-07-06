"""Load historical snapshot paths from existing bot tables."""

from __future__ import annotations

import sqlite3

from bot.research.features import get_research_markets, load_market_observations
from bot.research.market_behavior.config import MIN_OBS_PER_MARKET


def list_markets(conn: sqlite3.Connection, *, min_obs: int | None = None) -> list[str]:
    return get_research_markets(conn, min_obs=min_obs or MIN_OBS_PER_MARKET)


def load_market_path(conn: sqlite3.Connection, market_slug: str) -> list[dict]:
    return load_market_observations(conn, market_slug)
