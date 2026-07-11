"""Task J — historical replay reports."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.historical_replay.ai_critic import ai_critic_replay_report
from bot.research.market_events.historical_replay.candle_backfill import historical_candle_coverage
from bot.research.market_events.historical_replay.context_linker import link_replay_context
from bot.research.market_events.historical_replay.coverage import historical_replay_coverage
from bot.research.market_events.historical_replay.strategy_matrix import strategy_matrix_report


def historical_shock_report(conn: Any, *, run_tag: str) -> str:
    rows = conn.execute(
        """
        SELECT detector_id, COUNT(*) AS n
        FROM market_events_replay_shocks s
        JOIN market_events_replay_runs r ON r.id = s.run_id
        WHERE r.run_tag = ?
        GROUP BY detector_id ORDER BY n DESC
        """,
        (run_tag,),
    ).fetchall()
    total = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_replay_shocks s
        JOIN market_events_replay_runs r ON r.id = s.run_id WHERE r.run_tag = ?
        """,
        (run_tag,),
    ).fetchone()
    lines = [
        "HISTORICAL SHOCK REPORT",
        f"run_tag: {run_tag}",
        "mode: HISTORICAL REPLAY (isolated from production market_events)",
        f"replay_shocks: {int(total['n'] if total else 0)}",
        "",
    ]
    for r in rows:
        lines.append(f"  {r['detector_id']}: {r['n']}")
    return "\n".join(lines)


def historical_reversal_report(conn: Any, *, run_tag: str) -> str:
    rows = conn.execute(
        """
        SELECT p.path_class, COUNT(*) AS n, AVG(p.reversal_pct) AS avg_rev
        FROM market_events_replay_path_metrics p
        JOIN market_events_replay_shocks s ON s.id = p.shock_id
        JOIN market_events_replay_runs r ON r.id = s.run_id
        WHERE r.run_tag = ? AND p.horizon_sec = 300
        GROUP BY p.path_class
        """,
        (run_tag,),
    ).fetchall()
    lines = [
        "HISTORICAL REVERSAL REPORT",
        f"run_tag: {run_tag}",
        "horizon: 300s",
        "",
    ]
    if not rows:
        lines.append("No path metrics.")
    for r in rows:
        lines.append(f"  {r['path_class']}: n={r['n']} avg_reversal={r['avg_rev']:.3f}%")
    return "\n".join(lines)


def historical_context_report(conn: Any, *, run_tag: str) -> str:
    n = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_replay_context_links c
        JOIN market_events_replay_shocks s ON s.id = c.shock_id
        JOIN market_events_replay_runs r ON r.id = s.run_id
        WHERE r.run_tag = ?
        """,
        (run_tag,),
    ).fetchone()
    lines = [
        "HISTORICAL CONTEXT REPORT",
        f"run_tag: {run_tag}",
        "no-lookahead: context_ts < event_ts enforced",
        f"context_links: {int(n['n'] if n else 0)}",
    ]
    return "\n".join(lines)
