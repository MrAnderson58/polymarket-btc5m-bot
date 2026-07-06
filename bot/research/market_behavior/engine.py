"""Orchestrate market behavior analysis over historical snapshots."""

from __future__ import annotations

import sqlite3

from bot.research.market_behavior.analyzer import (
    aggregate_tp_samples,
    analyze_late_windows,
    analyze_market_summary,
    collect_tp_samples,
)
from bot.research.market_behavior.config import MIN_OBS_PER_MARKET
from bot.research.market_behavior.models import AnalysisReport
from bot.research.market_behavior.observations import list_markets, load_market_path
from bot.research.market_behavior.storage import store_report


def run_analysis(
    conn: sqlite3.Connection,
    *,
    min_obs: int | None = None,
    market_limit: int | None = None,
    persist: bool = True,
) -> AnalysisReport:
    """Analyze v4_shadow_observations history; optionally persist to mb_* tables."""
    slugs = list_markets(conn, min_obs=min_obs or MIN_OBS_PER_MARKET)
    if market_limit is not None:
        slugs = slugs[:market_limit]

    report = AnalysisReport(markets_analyzed=0, markets_skipped=0)
    all_tp_samples = []

    for slug in slugs:
        observations = load_market_path(conn, slug)
        if len(observations) < (min_obs or MIN_OBS_PER_MARKET):
            report.markets_skipped += 1
            continue

        summary = analyze_market_summary(slug, observations)
        report.summaries.append(summary)
        report.late_windows.extend(analyze_late_windows(slug, observations))
        all_tp_samples.extend(collect_tp_samples(observations))
        report.markets_analyzed += 1

        if summary.winning_side == "YES":
            report.yes_wins += 1
        elif summary.winning_side == "NO":
            report.no_wins += 1
        else:
            report.ties += 1

    report.tp_aggregates = aggregate_tp_samples(all_tp_samples)

    if persist:
        store_report(conn, report)

    return report
