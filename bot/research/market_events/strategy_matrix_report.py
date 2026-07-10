"""Full strategy matrix visibility — R1-R5 x EXIT_A-E."""

from __future__ import annotations

import statistics
from typing import Any

from bot.research.market_events.db_helpers import scalar
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.event_types import EXIT_IDS, REVERSAL_IDS
from bot.research.market_events.lifecycle_decisions import load_lifecycle_decisions


def shock_strategy_matrix_report(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    events = conn.execute(
        "SELECT id FROM market_events WHERE event_ts >= ?",
        (since,),
    ).fetchall()
    event_ids = [int(e["id"]) for e in events]

    lines = [
        "SHOCK STRATEGY MATRIX REPORT",
        f"window_days: {days}",
        f"events: {len(event_ids)}",
        "",
        "=== REVERSAL MATRIX (all variants) ===",
        f"{'variant':<6} {'cand':>5} {'reject':>7} {'activ':>6} {'enter':>6} {'exp_n':>6} "
        f"{'open':>5} {'closed':>7} {'win%':>6} {'mean':>8} {'med':>8} {'mfe':>8} {'mae':>8}",
    ]

    for rv in REVERSAL_IDS:
        stats = _matrix_row(conn, since, reversal_variant=rv)
        lines.append(_format_row(rv, stats))

    lines.extend(["", "=== PRE-RUN REVERSAL REJECTIONS (lifecycle_decisions) ==="])
    for rv in REVERSAL_IDS:
        rejected = conn.execute(
            """
            SELECT COUNT(*) AS n FROM market_event_lifecycle_decisions d
            JOIN market_events e ON e.id = d.event_id
            WHERE e.event_ts >= ? AND d.stage = ? AND d.status = 'rejected'
            """,
            (since, f"REVERSAL_{rv}"),
        ).fetchone()
        confirmed = conn.execute(
            """
            SELECT COUNT(*) AS n FROM market_event_lifecycle_decisions d
            JOIN market_events e ON e.id = d.event_id
            WHERE e.event_ts >= ? AND d.stage = ? AND d.status = 'confirmed'
            """,
            (since, f"REVERSAL_{rv}"),
        ).fetchone()
        lines.append(
            f"  R{rv[-1]}: rejected={scalar(rejected)} confirmed={scalar(confirmed)} "
            f"(pre-run, separate from paper_strategy_runs)",
        )

    lines.extend(["", "=== BY EXIT VARIANT (closed runs) ==="])
    for ex in EXIT_IDS:
        stats = _matrix_row(conn, since, exit_variant=ex)
        lines.append(_format_row(ex, stats))

    lines.extend(["", "=== COMBINED (reversal x exit) ==="])
    for rv in REVERSAL_IDS:
        for ex in EXIT_IDS:
            stats = _matrix_row(conn, since, reversal_variant=rv, exit_variant=ex)
            if stats["candidates"] > 0 or stats["closed"] > 0:
                lines.append(_format_row(f"{rv}x{ex}", stats))

    return "\n".join(lines)


def _matrix_row(
    conn: Any,
    since: int,
    *,
    reversal_variant: str | None = None,
    exit_variant: str | None = None,
) -> dict[str, Any]:
    base = """
        SELECT r.* FROM paper_strategy_runs r
        JOIN market_events e ON e.id = r.event_id
        WHERE e.event_ts >= ?
    """
    params: list[Any] = [since]
    if reversal_variant:
        base += " AND r.reversal_variant = ?"
        params.append(reversal_variant)
    if exit_variant:
        base += " AND r.exit_variant = ?"
        params.append(exit_variant)
    rows = conn.execute(base, params).fetchall()

    rejected = 0
    if reversal_variant and not exit_variant:
        rejected = int(scalar(conn.execute(
            """
            SELECT COUNT(*) AS n FROM market_event_lifecycle_decisions d
            JOIN market_events e ON e.id = d.event_id
            WHERE e.event_ts >= ? AND d.stage = ? AND d.status = 'rejected'
            """,
            (since, f"REVERSAL_{reversal_variant}"),
        ).fetchone()))

    entered = [r for r in rows if r["entry_ts"]]
    closed = [r for r in rows if r["exit_ts"]]
    open_r = [r for r in rows if r["entry_ts"] and not r["exit_ts"]]
    waiting = [r for r in rows if not r["entry_ts"]]
    rets = [float(r["net_return"]) for r in closed if r["net_return"] is not None]
    mfes = [float(r["mfe"]) for r in closed if r["mfe"] is not None]
    maes = [float(r["mae"]) for r in closed if r["mae"] is not None]
    wins = [r for r in rets if r > 0]

    return {
        "candidates": len(rows) + rejected,
        "rejected": rejected,
        "activated": len(rows),
        "entered": len(entered),
        "expired_no_entry": len(waiting),
        "open": len(open_r),
        "closed": len(closed),
        "win_rate": len(wins) / len(rets) if rets else 0.0,
        "mean_ret": statistics.mean(rets) if rets else None,
        "median_ret": statistics.median(rets) if rets else None,
        "mfe": statistics.mean(mfes) if mfes else None,
        "mae": statistics.mean(maes) if maes else None,
    }


def _format_row(label: str, s: dict[str, Any]) -> str:
    mean = f"{s['mean_ret']:.3f}" if s["mean_ret"] is not None else "-"
    med = f"{s['median_ret']:.3f}" if s["median_ret"] is not None else "-"
    mfe = f"{s['mfe']:.3f}" if s["mfe"] is not None else "-"
    mae = f"{s['mae']:.3f}" if s["mae"] is not None else "-"
    win = f"{s['win_rate']:.0%}" if s["closed"] else "-"
    return (
        f"{label:<6} {s['candidates']:>5} {s['rejected']:>7} {s['activated']:>6} {s['entered']:>6} "
        f"{s['expired_no_entry']:>6} {s['open']:>5} {s['closed']:>7} {win:>6} {mean:>8} {med:>8} "
        f"{mfe:>8} {mae:>8}"
    )
