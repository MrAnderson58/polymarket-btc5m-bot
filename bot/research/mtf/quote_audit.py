"""Quote and snapshot integrity checks — reason codes, no silent fixes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from bot.research.mtf.alignment import (
    WRONG_15M_WINDOW,
    WRONG_1H_MARKET,
    WRONG_DAILY_MARKET,
    is_15m_window_correct,
    is_1h_market_correct,
    is_daily_market_correct,
)
from bot.research.mtf.snapshots import TABLE

BID_GT_ASK = "BID_GT_ASK"
QUOTE_OUT_OF_RANGE = "QUOTE_OUT_OF_RANGE"
YES_NO_COMPLEMENT_VIOLATION = "YES_NO_COMPLEMENT_VIOLATION"
STALE_QUOTE = "STALE_QUOTE"
FROZEN_QUOTE = "FROZEN_QUOTE"
MISSING_QUOTE = "MISSING_QUOTE"
BOUNDARY_SWITCH = "BOUNDARY_SWITCH"

STALE_QUOTE_SEC = 120
FROZEN_REPEAT_COUNT = 5
MAX_ISSUES_PER_TF = 500


@dataclass
class QuoteIssue:
    snapshot_id: int
    timestamp: int
    timeframe: str
    reason: str
    detail: str = ""


@dataclass
class TfCoverageStats:
    slug_coverage: int = 0
    quote_coverage: int = 0
    strike_coverage: int = 0
    seconds_left_coverage: int = 0
    unique_markets: int = 0
    invalid_bid_ask: int = 0
    stale_quote: int = 0
    frozen_quote: int = 0
    missing_quote: int = 0
    boundary_switch: int = 0
    wrong_window: int = 0
    issues: list[QuoteIssue] = field(default_factory=list)


def _check_quote_pair(
    bid: float | None,
    ask: float | None,
    *,
    snapshot_id: int,
    ts: int,
    timeframe: str,
) -> list[QuoteIssue]:
    issues: list[QuoteIssue] = []
    if bid is None and ask is None:
        return issues
    if bid is None or ask is None:
        issues.append(QuoteIssue(snapshot_id, ts, timeframe, MISSING_QUOTE, "partial quote"))
        return issues
    if bid > ask:
        issues.append(
            QuoteIssue(snapshot_id, ts, timeframe, BID_GT_ASK, f"bid={bid} ask={ask}")
        )
    for label, val in (("bid", bid), ("ask", ask)):
        if val < 0 or val > 1:
            issues.append(
                QuoteIssue(snapshot_id, ts, timeframe, QUOTE_OUT_OF_RANGE, f"{label}={val}")
            )
    return issues


def _check_yes_no_complement(
    yes_ask: float | None,
    no_ask: float | None,
    *,
    snapshot_id: int,
    ts: int,
    timeframe: str,
    tolerance: float = 0.15,
) -> list[QuoteIssue]:
    if yes_ask is None or no_ask is None:
        return []
    total = yes_ask + no_ask
    if total < 1.0 - tolerance or total > 1.0 + tolerance:
        return [
            QuoteIssue(
                snapshot_id, ts, timeframe, YES_NO_COMPLEMENT_VIOLATION,
                f"yes_ask+no_ask={total:.3f}",
            )
        ]
    return []


def audit_tf_row(
    row: sqlite3.Row,
    timeframe: str,
    *,
    prev_row: sqlite3.Row | None = None,
    repeat_counts: dict[tuple[str, float, float], int] | None = None,
) -> TfCoverageStats:
    prefix = timeframe
    slug_col = f"market_{prefix}_slug"
    stats = TfCoverageStats()
    slug = row[slug_col]
    if not slug:
        return stats

    stats.slug_coverage = 1
    stats.unique_markets = 1

    bid = row[f"market_{prefix}_yes_bid"]
    ask = row[f"market_{prefix}_yes_ask"]
    no_bid = row[f"market_{prefix}_no_bid"]
    no_ask = row[f"market_{prefix}_no_ask"]
    strike = row[f"market_{prefix}_strike"]
    sl = row[f"market_{prefix}_seconds_left"]
    ts = int(row["timestamp"])
    snap_id = int(row["id"])

    if ask is not None or bid is not None:
        stats.quote_coverage = 1
    if strike is not None:
        stats.strike_coverage = 1
    if sl is not None:
        stats.seconds_left_coverage = 1

    issues = _check_quote_pair(bid, ask, snapshot_id=snap_id, ts=ts, timeframe=timeframe)
    issues += _check_yes_no_complement(ask, no_ask, snapshot_id=snap_id, ts=ts, timeframe=timeframe)
    stats.invalid_bid_ask = sum(1 for i in issues if i.reason == BID_GT_ASK)
    stats.missing_quote = sum(1 for i in issues if i.reason == MISSING_QUOTE)

    if prev_row and prev_row[slug_col] != slug:
        stats.boundary_switch = 1
        issues.append(
            QuoteIssue(snap_id, ts, timeframe, BOUNDARY_SWITCH, f"{prev_row[slug_col]} -> {slug}")
        )

    if repeat_counts is not None and bid is not None and ask is not None:
        key = (slug, bid, ask)
        repeat_counts[key] = repeat_counts.get(key, 0) + 1
        if repeat_counts[key] >= FROZEN_REPEAT_COUNT:
            stats.frozen_quote = 1
            issues.append(
                QuoteIssue(snap_id, ts, timeframe, FROZEN_QUOTE, f"repeated {repeat_counts[key]}x")
            )

    if timeframe == "15m" and not is_15m_window_correct(slug, ts):
        stats.wrong_window = 1
        issues.append(QuoteIssue(snap_id, ts, timeframe, WRONG_15M_WINDOW, slug))
    elif timeframe == "1h" and not is_1h_market_correct(slug, ts):
        stats.wrong_window = 1
        issues.append(QuoteIssue(snap_id, ts, timeframe, WRONG_1H_MARKET, slug))
    elif timeframe == "daily" and not is_daily_market_correct(slug, ts):
        stats.wrong_window = 1
        issues.append(QuoteIssue(snap_id, ts, timeframe, WRONG_DAILY_MARKET, slug))

    stats.issues = issues
    return stats


def aggregate_tf_stats(rows, timeframe: str) -> TfCoverageStats:
    total = TfCoverageStats()
    slug_set: set[str] = set()
    prev: sqlite3.Row | None = None
    repeat_counts: dict[tuple[str, float, float], int] = {}

    for row in rows:
        s = audit_tf_row(row, timeframe, prev_row=prev, repeat_counts=repeat_counts)
        total.slug_coverage += s.slug_coverage
        total.quote_coverage += s.quote_coverage
        total.strike_coverage += s.strike_coverage
        total.seconds_left_coverage += s.seconds_left_coverage
        total.invalid_bid_ask += s.invalid_bid_ask
        total.stale_quote += s.stale_quote
        total.frozen_quote += s.frozen_quote
        total.missing_quote += s.missing_quote
        total.boundary_switch += s.boundary_switch
        total.wrong_window += s.wrong_window
        for issue in s.issues:
            if len(total.issues) < MAX_ISSUES_PER_TF:
                total.issues.append(issue)
        slug = row[f"market_{timeframe}_slug"]
        if slug:
            slug_set.add(slug)
        prev = row

    total.unique_markets = len(slug_set)
    return total


def aggregate_tf_stats_stream(
    conn: sqlite3.Connection,
    timeframe: str,
    *,
    table: str = TABLE,
    progress_every: int = 0,
    progress_fn=None,
) -> TfCoverageStats:
    """Stream snapshots from DB — avoids loading all rows into memory."""
    total = TfCoverageStats()
    slug_set: set[str] = set()
    prev: sqlite3.Row | None = None
    repeat_counts: dict[tuple[str, float, float], int] = {}
    processed = 0

    for row in conn.execute(f"SELECT * FROM {table} ORDER BY timestamp ASC"):
        processed += 1
        if progress_every and progress_fn and processed % progress_every == 0:
            progress_fn("scan_snapshots", processed)

        s = audit_tf_row(row, timeframe, prev_row=prev, repeat_counts=repeat_counts)
        total.slug_coverage += s.slug_coverage
        total.quote_coverage += s.quote_coverage
        total.strike_coverage += s.strike_coverage
        total.seconds_left_coverage += s.seconds_left_coverage
        total.invalid_bid_ask += s.invalid_bid_ask
        total.stale_quote += s.stale_quote
        total.frozen_quote += s.frozen_quote
        total.missing_quote += s.missing_quote
        total.boundary_switch += s.boundary_switch
        total.wrong_window += s.wrong_window
        for issue in s.issues:
            if len(total.issues) < MAX_ISSUES_PER_TF:
                total.issues.append(issue)
        slug = row[f"market_{timeframe}_slug"]
        if slug:
            slug_set.add(slug)
        prev = row

    total.unique_markets = len(slug_set)
    return total
