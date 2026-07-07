"""Chronological and quality filters for strategy simulator market sets."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from statistics import median

from bot.collector_diagnostics import _slug_window_start, analyze_market_gaps
from bot.research.features import get_research_markets, load_market_observations
from bot.research.strategy_simulator.config import MIN_OBS_PER_MARKET


@dataclass(frozen=True)
class MarketFilter:
    market_start_ts: int | None = None
    market_end_ts: int | None = None
    min_obs_per_market: int | None = None
    max_median_gap: float | None = None
    min_coverage_span: int | None = None
    completed_only: bool = False


def _window_start(slug: str) -> int:
    return _slug_window_start(slug)


def _completed_market(path: list[dict], *, min_span: int = 240) -> bool:
    if len(path) < 2:
        return False
    last = path[-1]
    if last.get("seconds_left") is not None and int(last["seconds_left"]) <= 15:
        return True
    ts0 = int(path[0]["timestamp"])
    ts1 = int(path[-1]["timestamp"])
    return ts1 - ts0 >= min_span


def market_passes_filter(path: list[dict], filt: MarketFilter) -> bool:
    if not path:
        return False
    slug = str(path[0]["market_slug"])
    ws = _window_start(slug)
    if filt.market_start_ts is not None and ws < filt.market_start_ts:
        return False
    if filt.market_end_ts is not None and ws > filt.market_end_ts:
        return False
    min_obs = filt.min_obs_per_market or 0
    if len(path) < min_obs:
        return False
    if filt.completed_only or filt.max_median_gap is not None or filt.min_coverage_span:
        if not _completed_market(path, min_span=filt.min_coverage_span or 240):
            return False
    if filt.max_median_gap is not None or filt.min_coverage_span is not None:
        stats = analyze_market_gaps(path)
        if stats is None:
            return False
        if filt.max_median_gap is not None and stats.median_gap_sec > filt.max_median_gap:
            return False
        if filt.min_coverage_span is not None and stats.collector_span_sec < filt.min_coverage_span:
            return False
    return True


def list_filtered_market_paths(
    conn: sqlite3.Connection,
    filt: MarketFilter,
    *,
    base_min_obs: int | None = None,
) -> dict[str, list[dict]]:
    floor = base_min_obs or filt.min_obs_per_market or MIN_OBS_PER_MARKET
    slugs = get_research_markets(conn, min_obs=min(floor, 5))
    slugs = sorted(slugs, key=_window_start)
    paths: dict[str, list[dict]] = {}
    for slug in slugs:
        path = load_market_observations(conn, slug)
        if market_passes_filter(path, filt):
            paths[slug] = path
    return paths


def add_market_filter_args(parser) -> None:
    parser.add_argument(
        "--market-start-ts",
        type=int,
        default=None,
        help="Include markets with window_start_ts >= value",
    )
    parser.add_argument(
        "--market-end-ts",
        type=int,
        default=None,
        help="Include markets with window_start_ts <= value",
    )
    parser.add_argument(
        "--min-obs-per-market",
        type=int,
        default=None,
        help="Minimum observations per market (overrides --min-obs when set)",
    )
    parser.add_argument(
        "--max-median-gap",
        type=float,
        default=None,
        help="Max median inter-observation gap (seconds) on completed markets",
    )
    parser.add_argument(
        "--min-coverage-span",
        type=int,
        default=None,
        help="Min timestamp span (seconds) on completed markets",
    )
    parser.add_argument(
        "--completed-only",
        action="store_true",
        help="Only include completed 5m markets",
    )


def market_filter_from_args(args) -> MarketFilter:
    min_obs = getattr(args, "min_obs_per_market", None)
    if min_obs is None:
        min_obs = getattr(args, "min_obs", None)
    return MarketFilter(
        market_start_ts=getattr(args, "market_start_ts", None),
        market_end_ts=getattr(args, "market_end_ts", None),
        min_obs_per_market=min_obs,
        max_median_gap=getattr(args, "max_median_gap", None),
        min_coverage_span=getattr(args, "min_coverage_span", None),
        completed_only=getattr(args, "completed_only", False),
    )
