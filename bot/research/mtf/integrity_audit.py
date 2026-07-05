"""Production MTF snapshot integrity audit CLI backend."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.mtf.alignment import (
    WRONG_15M_WINDOW,
    WRONG_1H_MARKET,
    WRONG_DAILY_MARKET,
)
from bot.research.mtf.metadata import ensure_metadata_table, list_metadata_by_source
from bot.research.mtf.quote_audit import aggregate_tf_stats
from bot.research.mtf.snapshots import TABLE, ensure_tables


def _fmt_tf_stats(stats: Any) -> dict[str, Any]:
    return {
        "slug_coverage": stats.slug_coverage,
        "quote_coverage": stats.quote_coverage,
        "strike_coverage": stats.strike_coverage,
        "seconds_left_coverage": stats.seconds_left_coverage,
        "unique_markets": stats.unique_markets,
        "invalid_bid_ask_count": stats.invalid_bid_ask,
        "stale_quote_count": stats.stale_quote,
        "frozen_quote_count": stats.frozen_quote,
        "missing_quote_count": stats.missing_quote,
        "boundary_switch_count": stats.boundary_switch,
    }


def audit_production(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_tables(conn)
    ensure_metadata_table(conn)

    rows = conn.execute(
        f"SELECT * FROM {TABLE} ORDER BY timestamp ASC"
    ).fetchall()

    total = len(rows)
    first_ts = rows[0]["timestamp"] if rows else None
    last_ts = rows[-1]["timestamp"] if rows else None
    duration = (last_ts - first_ts) if first_ts is not None and last_ts is not None else 0

    s15 = aggregate_tf_stats(rows, "15m")
    s1h = aggregate_tf_stats(rows, "1h")
    sd = aggregate_tf_stats(rows, "daily")

    strike_sources = [
        {"timeframe": r["timeframe"], "strike_source": r["strike_source"], "count": r["n"]}
        for r in list_metadata_by_source(conn)
    ]

    return {
        "snapshots": {
            "total": total,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "duration_seconds": duration,
        },
        "15m": _fmt_tf_stats(s15),
        "1h": _fmt_tf_stats(s1h),
        "daily": _fmt_tf_stats(sd),
        "alignment": {
            "wrong_15m_window_count": s15.wrong_window,
            "wrong_1h_market_count": s1h.wrong_window,
            "wrong_daily_market_count": sd.wrong_window,
        },
        "strike_sources": strike_sources,
        "top_issues": _top_issue_summary(s15, s1h, sd),
    }


def _top_issue_summary(*stats_list: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for stats in stats_list:
        for issue in stats.issues:
            counts[issue.reason] = counts.get(issue.reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1])[:15])


def render_production_audit(report: dict[str, Any]) -> str:
    s = report["snapshots"]
    lines = [
        "SNAPSHOTS",
        f"  total: {s['total']}",
        f"  first timestamp: {s['first_timestamp']}",
        f"  last timestamp: {s['last_timestamp']}",
        f"  duration: {s['duration_seconds']}s",
        "",
    ]

    for tf in ("15m", "1h", "daily"):
        block = report[tf]
        lines.append(tf.upper())
        for k, v in block.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    align = report["alignment"]
    lines.extend([
        "ALIGNMENT",
        f"  wrong 15m window count: {align['wrong_15m_window_count']}",
        f"  wrong 1h market count: {align['wrong_1h_market_count']}",
        f"  wrong daily market count: {align['wrong_daily_market_count']}",
        "",
        "STRIKE SOURCES",
    ])
    for row in report["strike_sources"]:
        lines.append(f"  {row['timeframe']} / {row['strike_source']}: {row['count']}")
    if not report["strike_sources"]:
        lines.append("  (none cached yet)")

    if report.get("top_issues"):
        lines.extend(["", "TOP ISSUE REASONS"])
        for reason, count in report["top_issues"].items():
            lines.append(f"  {reason}: {count}")

    return "\n".join(lines)
