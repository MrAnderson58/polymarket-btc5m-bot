"""Load 15m observation paths from MTF snapshots — no look-ahead."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from bot.research.mtf.discovery import parse_15m_window_start_ts
from bot.research.mtf.research_15m.features import build_obs15m
from bot.research.mtf.research_15m.models import Obs15m
from bot.research.mtf.snapshots import TABLE


def load_15m_market_paths(conn: sqlite3.Connection) -> dict[str, list[Obs15m]]:
    rows = conn.execute(
        f"""
        SELECT *
        FROM {TABLE}
        WHERE market_15m_slug IS NOT NULL
          AND market_15m_yes_ask IS NOT NULL
        ORDER BY timestamp ASC
        """
    ).fetchall()

    raw_by_slug: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        slug = row["market_15m_slug"]
        ws = parse_15m_window_start_ts(slug)
        if ws is None:
            continue
        ts = int(row["timestamp"])
        if not (ws <= ts < ws + 900):
            continue
        raw_by_slug[slug].append(row)

    paths: dict[str, list[Obs15m]] = {}
    for slug, snap_rows in raw_by_slug.items():
        obs_list = [build_obs15m(conn, r) for r in snap_rows]
        obs_list.sort(key=lambda o: o.timestamp)
        if len(obs_list) >= 3:
            paths[slug] = obs_list
    return paths


def list_markets_chronological(paths: dict[str, list[Obs15m]]) -> list[str]:
    return sorted(paths.keys(), key=lambda s: paths[s][0].window_start_ts)
