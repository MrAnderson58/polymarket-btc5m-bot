"""Human-readable market behavior research report."""

from __future__ import annotations

import sqlite3
from statistics import mean

from bot.research.market_behavior.config import LATE_WINDOW_BUCKETS, TP_LEVELS
from bot.research.market_behavior.models import AnalysisReport
from bot.research.market_behavior.schema import (
    LATE_WINDOW_TABLE,
    SUMMARY_TABLE,
    TP_PROBABILITY_TABLE,
)


def render_report(report: AnalysisReport) -> str:
    lines = [
        "MARKET BEHAVIOR RESEARCH REPORT",
        "=" * 40,
        f"markets analyzed: {report.markets_analyzed}",
        f"markets skipped: {report.markets_skipped}",
        "",
        "OUTCOMES",
        f"  YES wins: {report.yes_wins}",
        f"  NO wins:  {report.no_wins}",
        f"  TIE/UNK:  {report.ties}",
    ]

    deltas = [s.btc_delta_final for s in report.summaries if s.btc_delta_final is not None]
    if deltas:
        lines.extend([
            "",
            "BTC DELTA VS STRIKE (final)",
            f"  mean: {mean(deltas):+.2f} USD",
            f"  min:  {min(deltas):+.2f} USD",
            f"  max:  {max(deltas):+.2f} USD",
        ])

    yes_ranges = [
        (s.yes_ask_min, s.yes_ask_max) for s in report.summaries
        if s.yes_ask_min is not None and s.yes_ask_max is not None
    ]
    if yes_ranges:
        lines.extend([
            "",
            "YES ASK RANGE (per-market min/max aggregated)",
            f"  global min ask: {min(r[0] for r in yes_ranges):.3f}",
            f"  global max ask: {max(r[1] for r in yes_ranges):.3f}",
        ])

    no_ranges = [
        (s.no_ask_min, s.no_ask_max) for s in report.summaries
        if s.no_ask_min is not None and s.no_ask_max is not None
    ]
    if no_ranges:
        lines.extend([
            "",
            "NO ASK RANGE (per-market min/max aggregated)",
            f"  global min ask: {min(r[0] for r in no_ranges):.3f}",
            f"  global max ask: {max(r[1] for r in no_ranges):.3f}",
        ])

    lines.extend(["", "LATE WINDOW BTC DELTA (mean by seconds_left bucket)"])
    for bucket in LATE_WINDOW_BUCKETS:
        bucket_deltas = [
            lw.btc_delta_vs_strike for lw in report.late_windows
            if lw.seconds_bucket == bucket and lw.btc_delta_vs_strike is not None
        ]
        if bucket_deltas:
            lines.append(f"  {bucket:>2}s left: mean delta {mean(bucket_deltas):+.2f} USD (n={len(bucket_deltas)})")
        else:
            lines.append(f"  {bucket:>2}s left: no data")

    lines.extend(["", "LATE WINDOW BTC MOVE TO CLOSE (mean by bucket)"])
    for bucket in LATE_WINDOW_BUCKETS:
        moves = [
            lw.btc_move_to_close for lw in report.late_windows
            if lw.seconds_bucket == bucket and lw.btc_move_to_close is not None
        ]
        if moves:
            lines.append(f"  {bucket:>2}s left: mean move {mean(moves):+.2f} USD (n={len(moves)})")

    lines.extend(["", "TP REACH PROBABILITY (after entry ask, forward to close)"])
    for side in ("YES", "NO"):
        side_aggs = [a for a in report.tp_aggregates if a.side == side and a.sample_count >= 10]
        if not side_aggs:
            lines.append(f"  [{side}] insufficient samples")
            continue
        lines.append(f"  [{side}]")
        by_bucket: dict[str, list] = {}
        for a in side_aggs:
            by_bucket.setdefault(a.entry_bucket, []).append(a)
        for bucket in sorted(by_bucket.keys()):
            rows = by_bucket[bucket]
            mid = rows[0].entry_price_mid
            parts = []
            for tp in TP_LEVELS:
                match = next((r for r in rows if abs(r.tp_level - tp) < 1e-6), None)
                if match and match.sample_count >= 10:
                    parts.append(f"TP{tp:.2f}={match.reach_probability:.0%}")
            if parts:
                lines.append(f"    entry {bucket} (~{mid:.2f}): " + ", ".join(parts[:5]))

    if report.summaries:
        lines.extend(["", "SAMPLE MARKETS (last 5)"])
        for s in report.summaries[-5:]:
            lines.append(
                f"  {s.market_slug}: {s.winning_side} "
                f"delta={s.btc_delta_final:+.1f} obs={s.observation_count}"
                if s.btc_delta_final is not None else
                f"  {s.market_slug}: {s.winning_side} obs={s.observation_count}"
            )

    lines.append("")
    lines.append("Observe-only. No execution impact.")
    return "\n".join(lines)


def load_stored_summary_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {SUMMARY_TABLE}").fetchone()
    return int(row["n"]) if row else 0


def render_stored_tp_table(conn: sqlite3.Connection, *, min_samples: int = 20) -> str:
    rows = conn.execute(
        f"""
        SELECT side, entry_bucket, tp_level, sample_count, reach_probability
        FROM {TP_PROBABILITY_TABLE}
        WHERE sample_count >= ?
        ORDER BY side, entry_price_mid, tp_level
        """,
        (min_samples,),
    ).fetchall()
    if not rows:
        return "No stored TP probability rows (run report first)."
    lines = ["STORED TP PROBABILITIES", "-" * 30]
    for r in rows:
        lines.append(
            f"  {r['side']} entry={r['entry_bucket']} TP={r['tp_level']:.2f} "
            f"p={r['reach_probability']:.1%} n={r['sample_count']}"
        )
    return "\n".join(lines)
