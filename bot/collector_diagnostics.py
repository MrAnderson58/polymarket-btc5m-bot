"""V4 shadow collector diagnostics — observe-only audit and cycle metrics."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from statistics import median

from bot.database import connect
from bot.research.features import get_research_markets, load_market_observations


def _slug_window_start(slug: str) -> int:
    try:
        return int(slug.rsplit("-", 1)[-1])
    except ValueError:
        return 0


@dataclass
class MarketGapStats:
    market_slug: str
    window_start_ts: int
    obs_count: int
    median_gap_sec: float
    mean_gap_sec: float
    min_gap_sec: int
    max_gap_sec: int
    first_ts: int
    last_ts: int
    seconds_left_first: int | None
    seconds_left_last: int | None
    collector_span_sec: int


def _gaps(timestamps: list[int]) -> list[int]:
    if len(timestamps) < 2:
        return []
    return [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]


def analyze_market_gaps(path: list[dict]) -> MarketGapStats | None:
    if not path:
        return None
    slug = str(path[0]["market_slug"])
    window_start = int(path[0]["window_start_ts"])
    ts_list = [int(r["timestamp"]) for r in path]
    gap_list = _gaps(ts_list)
    return MarketGapStats(
        market_slug=slug,
        window_start_ts=window_start,
        obs_count=len(path),
        median_gap_sec=float(median(gap_list)) if gap_list else 0.0,
        mean_gap_sec=sum(gap_list) / len(gap_list) if gap_list else 0.0,
        min_gap_sec=min(gap_list) if gap_list else 0,
        max_gap_sec=max(gap_list) if gap_list else 0,
        first_ts=ts_list[0],
        last_ts=ts_list[-1],
        seconds_left_first=path[0].get("seconds_left"),
        seconds_left_last=path[-1].get("seconds_left"),
        collector_span_sec=ts_list[-1] - ts_list[0],
    )


def analyze_last_markets(
    conn: sqlite3.Connection,
    *,
    last_n: int = 20,
    min_obs: int = 5,
) -> list[MarketGapStats]:
    slugs = get_research_markets(conn, min_obs=min_obs)
    slugs.sort(key=_slug_window_start)
    selected = slugs[-last_n:] if last_n > 0 else slugs
    out: list[MarketGapStats] = []
    for slug in selected:
        path = load_market_observations(conn, slug)
        stats = analyze_market_gaps(path)
        if stats is not None:
            out.append(stats)
    return out


def _ensure_collector_cycles_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS v4_collector_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_started_at REAL NOT NULL,
            cycle_finished_at REAL NOT NULL,
            cycle_duration_ms REAL NOT NULL,
            market_slug TEXT,
            observation_inserted INTEGER NOT NULL DEFAULT 0,
            btc_price_ms REAL,
            strike_ms REAL,
            quotes_ms REAL,
            process_ms REAL,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_v4_collector_cycles_created
        ON v4_collector_cycles (created_at DESC)
        """
    )


def record_v4_cycle(
    conn: sqlite3.Connection,
    *,
    cycle_started_at: float,
    cycle_finished_at: float,
    market_slug: str | None,
    observation_inserted: bool,
    btc_price_ms: float | None = None,
    strike_ms: float | None = None,
    quotes_ms: float | None = None,
    process_ms: float | None = None,
) -> None:
    """Persist one V4 collector cycle timing row (observe-only diagnostics)."""
    _ensure_collector_cycles_table(conn)
    duration_ms = (cycle_finished_at - cycle_started_at) * 1000.0
    conn.execute(
        """
        INSERT INTO v4_collector_cycles (
            cycle_started_at, cycle_finished_at, cycle_duration_ms,
            market_slug, observation_inserted,
            btc_price_ms, strike_ms, quotes_ms, process_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cycle_started_at,
            cycle_finished_at,
            duration_ms,
            market_slug,
            1 if observation_inserted else 0,
            btc_price_ms,
            strike_ms,
            quotes_ms,
            process_ms,
            int(time.time()),
        ),
    )


def record_main_cycle(
    conn: sqlite3.Connection,
    *,
    cycle_started_at: float,
    cycle_finished_at: float,
    market_slug: str | None,
    mtf_collected: bool,
) -> None:
    _ensure_collector_cycles_table(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS main_collector_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_started_at REAL NOT NULL,
            cycle_finished_at REAL NOT NULL,
            cycle_duration_ms REAL NOT NULL,
            market_slug TEXT,
            mtf_collected INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )
    duration_ms = (cycle_finished_at - cycle_started_at) * 1000.0
    conn.execute(
        """
        INSERT INTO main_collector_cycles (
            cycle_started_at, cycle_finished_at, cycle_duration_ms,
            market_slug, mtf_collected, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            cycle_started_at,
            cycle_finished_at,
            duration_ms,
            market_slug,
            1 if mtf_collected else 0,
            int(time.time()),
        ),
    )


def _format_density_report(
    stats: list[MarketGapStats],
    *,
    boundary_ts: int | None = None,
) -> str:
    if not stats:
        return "No markets with sufficient observations."

    obs_counts = [s.obs_count for s in stats]
    gaps = [s.median_gap_sec for s in stats if s.median_gap_sec > 0]
    lines = [
        f"Markets analyzed: {len(stats)}",
        f"obs/market: min={min(obs_counts)} median={median(obs_counts):.0f} "
        f"max={max(obs_counts)}",
    ]
    if gaps:
        lines.append(
            f"median gap (sec): min={min(gaps):.1f} median={median(gaps):.1f} "
            f"max={max(gaps):.1f}"
        )
    lines.append("")
    lines.append(f"{'window_start':>12}  {'obs':>4}  {'med_gap':>7}  {'span':>5}  slug")
    for s in stats:
        lines.append(
            f"{s.window_start_ts:>12}  {s.obs_count:>4}  {s.median_gap_sec:>7.1f}  "
            f"{s.collector_span_sec:>5}  {s.market_slug}"
        )

    if boundary_ts is not None:
        before = [s for s in stats if s.window_start_ts < boundary_ts]
        after = [s for s in stats if s.window_start_ts >= boundary_ts]
        if before and after:
            lines.append("")
            lines.append(f"Boundary window_start_ts={boundary_ts}")
            lines.append(
                f"  before: n={len(before)} "
                f"median_obs={median([b.obs_count for b in before]):.0f} "
                f"median_gap={median([b.median_gap_sec for b in before if b.median_gap_sec]):.1f}s"
            )
            lines.append(
                f"  after:  n={len(after)} "
                f"median_obs={median([a.obs_count for a in after]):.0f} "
                f"median_gap={median([a.median_gap_sec for a in after if a.median_gap_sec]):.1f}s"
            )

    lines.append("")
    lines.append("Per-market timestamp vs seconds_left (first/last row):")
    for s in stats[-5:]:
        lines.append(
            f"  {s.market_slug}: ts {s.first_ts}->{s.last_ts} | "
            f"seconds_left {s.seconds_left_first}->{s.seconds_left_last}"
        )
    return "\n".join(lines)


def cmd_v4_density(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit v4_shadow_observations density")
    parser.add_argument("--last-markets", type=int, default=20)
    parser.add_argument("--min-obs", type=int, default=5)
    parser.add_argument(
        "--boundary-ts",
        type=int,
        default=1782722400,
        help="Train/val split boundary (default: 2026-06-29 08:40 UTC)",
    )
    args = parser.parse_args(argv)

    with connect() as conn:
        stats = analyze_last_markets(
            conn, last_n=args.last_markets, min_obs=args.min_obs
        )
    print(_format_density_report(stats, boundary_ts=args.boundary_ts))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        return cmd_v4_density(argv)
    if argv[0] == "v4-density":
        return cmd_v4_density(argv[1:])
    print(f"Unknown command: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
