"""Pending reversal lifecycle report."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.shock_profiles import profile_name_for_symbol


def pending_reversal_report(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT p.*, e.classification, e.cross_classification,
               (SELECT COUNT(*) FROM market_event_context c WHERE c.event_id = p.event_id) AS ctx_n
        FROM market_events_pending_shocks p
        JOIN market_events e ON e.id = p.event_id
        WHERE p.detected_ts >= ?
        ORDER BY p.detected_ts DESC
        """,
        (since,),
    ).fetchall()

    lines = [
        "PENDING REVERSAL LIFECYCLE REPORT",
        f"days: {days}",
        "",
    ]
    if not rows:
        lines.append("No pending shock records in window.")
        return "\n".join(lines)

    by_phase: dict[str, int] = defaultdict(int)
    by_profile: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    latencies: list[int] = []

    for r in rows:
        phase = r["phase"]
        by_phase[phase] += 1
        prof = profile_name_for_symbol(r["symbol"])
        by_profile[prof][phase] += 1
        if r["confirm_latency_sec"]:
            latencies.append(int(r["confirm_latency_sec"]))

    lines.append("=== By phase ===")
    for phase, n in sorted(by_phase.items()):
        lines.append(f"  {phase}: {n}")

    lines.append("")
    lines.append("=== By asset profile ===")
    for prof, phases in sorted(by_profile.items()):
        parts = ", ".join(f"{p}={n}" for p, n in sorted(phases.items()))
        lines.append(f"  {prof}: {parts}")

    if latencies:
        lines.append("")
        lines.append(f"confirmation_latency_sec: min={min(latencies)} max={max(latencies)} "
                     f"avg={sum(latencies)/len(latencies):.0f} n={len(latencies)}")

    lines.append("")
    lines.append("=== Recent events ===")
    for r in rows[:20]:
        ctx = "linked" if r["ctx_n"] else "no_context"
        lines.append(
            f"  id={r['event_id']} {r['symbol']} {r['direction']} phase={r['phase']} "
            f"shock={r['shock_return_pct']:.2f}% "
            f"confirmed={r['confirmed_reversal'] or '-'} "
            f"latency={r['confirm_latency_sec'] or '-'}s "
            f"class={r['classification']} cross={r['cross_classification'] or '-'} "
            f"context={ctx}",
        )
    return "\n".join(lines)
