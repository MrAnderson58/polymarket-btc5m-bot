"""Production MTF snapshot integrity audit CLI backend."""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from typing import Any, Callable

from bot.research.mtf.metadata import ensure_metadata_table, list_metadata_by_source
from bot.research.mtf.quote_audit import aggregate_tf_stats_stream
from bot.research.mtf.snapshots import TABLE, ensure_tables

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int | None], None]


def _default_progress(stage: str, count: int | None = None) -> None:
    msg = f"[audit-production] {stage}" + (f" ({count:,} rows)" if count is not None else "")
    print(msg, file=sys.stderr, flush=True)
    logger.info(msg)


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
        "issues_captured": len(stats.issues),
    }


def audit_production(
    conn: sqlite3.Connection,
    *,
    offline: bool = True,
    progress: bool = True,
    progress_fn: ProgressFn | None = None,
    progress_every: int = 25_000,
) -> dict[str, Any]:
    """Audit local MTF snapshot DB. Offline mode uses no external HTTP (default)."""
    started = time.monotonic()
    log = progress_fn or (_default_progress if progress else lambda *_: None)

    ensure_tables(conn)
    log("init_tables")
    ensure_metadata_table(conn)
    log("init_metadata")

    total = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    log("count_snapshots", total)

    bounds = conn.execute(
        f"SELECT MIN(timestamp), MAX(timestamp) FROM {TABLE}"
    ).fetchone()
    first_ts, last_ts = bounds[0], bounds[1]
    duration = (last_ts - first_ts) if first_ts is not None and last_ts is not None else 0

    def scan_progress(stage: str, count: int) -> None:
        log(stage, count)

    log("scan_15m")
    s15 = aggregate_tf_stats_stream(
        conn, "15m", progress_every=progress_every, progress_fn=scan_progress,
    )
    log("scan_1h")
    s1h = aggregate_tf_stats_stream(
        conn, "1h", progress_every=progress_every, progress_fn=scan_progress,
    )
    log("scan_daily")
    sd = aggregate_tf_stats_stream(
        conn, "daily", progress_every=progress_every, progress_fn=scan_progress,
    )
    log("strike_sources")
    strike_sources = [
        {"timeframe": r["timeframe"], "strike_source": r["strike_source"], "count": r["n"]}
        for r in list_metadata_by_source(conn)
    ]

    elapsed = round(time.monotonic() - started, 2)
    log("complete", int(elapsed * 1000))

    return {
        "mode": "offline" if offline else "online",
        "elapsed_seconds": elapsed,
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
        f"MODE: {report.get('mode', 'offline')} (elapsed {report.get('elapsed_seconds', '?')}s)",
        "",
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
