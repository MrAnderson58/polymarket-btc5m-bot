"""AI shadow evaluation and reports."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from bot.research.market_events.event_report import _days_ago_ts


def market_alert_audit(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT alert_type, sent, COUNT(*) AS n,
               AVG(latency_ms) AS avg_latency,
               SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors
        FROM market_event_alert_log
        WHERE created_at >= ?
        GROUP BY alert_type, sent
        ORDER BY alert_type
        """,
        (since,),
    ).fetchall()
    dupes = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
          SELECT dedupe_key FROM market_event_alert_log
          WHERE created_at >= ? GROUP BY dedupe_key HAVING COUNT(*) > 1
        )
        """,
        (since,),
    ).fetchone()

    lines = [
        "MARKET ALERT AUDIT",
        f"days: {days}",
        "",
    ]
    if not rows:
        lines.append("No alerts in window.")
    else:
        for r in rows:
            lat = r["avg_latency"] or 0
            lines.append(
                f"  {r['alert_type']} sent={r['sent']} n={r['n']} "
                f"avg_latency_ms={lat:.0f} errors={r['errors']}",
            )
    lines.append(f"duplicate_dedupe_keys: {int(dupes['n'] if dupes else 0)}")
    return "\n".join(lines)


def ai_analysis_audit(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    jobs = conn.execute(
        """
        SELECT status, COUNT(*) AS n FROM market_event_analysis_jobs
        WHERE created_at >= ? GROUP BY status
        """,
        (since,),
    ).fetchall()
    analyses = conn.execute(
        """
        SELECT
          json_extract(structured_output_json, '$.analysis_status') AS analysis_status,
          COUNT(*) AS n
        FROM market_event_ai_analyses
        WHERE created_at >= ?
        GROUP BY analysis_status
        """,
        (since,),
    ).fetchall()
    avg_lat = conn.execute(
        "SELECT AVG(latency_ms) AS v FROM market_event_ai_analyses WHERE created_at >= ?",
        (since,),
    ).fetchone()
    ctx_cov = conn.execute(
        """
        SELECT AVG(json_array_length(context_ids_json)) AS avg_ctx
        FROM market_event_ai_analyses
        WHERE created_at >= ? AND context_ids_json IS NOT NULL
        """,
        (since,),
    ).fetchone()

    lines = [
        "AI ANALYSIS AUDIT",
        f"days: {days}",
        "",
        "Jobs:",
    ]
    for r in jobs:
        lines.append(f"  {r['status']}: {r['n']}")
    if not jobs:
        lines.append("  (none)")
    lines.append("")
    lines.append("Analyses:")
    for r in analyses:
        lines.append(f"  {r['analysis_status'] or 'unknown'}: {r['n']}")
    if not analyses:
        lines.append("  (none)")
    if avg_lat and avg_lat["v"] is not None:
        lines.append(f"avg_latency_ms: {avg_lat['v']:.0f}")
    else:
        lines.append("avg_latency_ms: n/a")
    if ctx_cov and ctx_cov["avg_ctx"] is not None:
        lines.append(f"avg_context_ids: {ctx_cov['avg_ctx']:.1f}")
    return "\n".join(lines)


def _paper_outcome_for_event(conn: Any, event_id: int) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT net_return, exit_ts FROM paper_strategy_runs
        WHERE event_id = ? AND exit_ts IS NOT NULL
        """,
        (event_id,),
    ).fetchall()
    if not rows:
        return None
    returns = [float(r["net_return"] or 0) for r in rows]
    return {
        "n": len(returns),
        "avg_net_return": sum(returns) / len(returns),
        "positive": sum(1 for x in returns if x > 0),
    }


def _continuation_correct(conn: Any, event_id: int, direction: str) -> bool | None:
    """True if post-shock price continued in shock direction (fade would lose)."""
    snap = conn.execute(
        """
        SELECT return_from_event FROM market_event_snapshots
        WHERE event_id = ? AND return_from_event IS NOT NULL
        ORDER BY offset_seconds DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not snap or snap["return_from_event"] is None:
        return None
    ret = float(snap["return_from_event"])
    if direction == "UP":
        return ret > 0.3
    if direction == "DOWN":
        return ret < -0.3
    return None


def ai_event_report(conn: Any, *, days: int = 7) -> str:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT a.event_id, a.reversal_bias, a.confidence, a.movement_interpretation,
               a.structured_output_json, e.return_pct, e.direction, e.symbol
        FROM market_event_ai_analyses a
        JOIN market_events e ON e.id = a.event_id
        WHERE a.created_at >= ?
          AND json_extract(a.structured_output_json, '$.analysis_status') = 'COMPLETE'
        ORDER BY a.created_at DESC
        """,
        (since,),
    ).fetchall()

    lines = [
        "AI EVENT REPORT (shadow evaluation — does not affect paper execution)",
        f"days: {days}",
        f"analyses_complete: {len(rows)}",
        "",
    ]
    if len(rows) < 5:
        lines.append("Insufficient sample for performance verdict (N < 5).")
        return "\n".join(lines)

    bias_counts: dict[str, int] = defaultdict(int)
    conf_buckets = {"low": 0, "mid": 0, "high": 0}
    fade_correct = fade_total = 0
    cont_correct = cont_total = 0
    wait_avoided_bad = wait_total = 0

    det_returns: list[float] = []
    ai_filter_returns: list[float] = []

    for r in rows:
        bias = r["reversal_bias"] or "NO_VIEW"
        bias_counts[bias] += 1
        c = float(r["confidence"] or 0)
        if c < 0.4:
            conf_buckets["low"] += 1
        elif c < 0.7:
            conf_buckets["mid"] += 1
        else:
            conf_buckets["high"] += 1

        outcome = _paper_outcome_for_event(conn, int(r["event_id"]))
        if outcome:
            det_returns.append(outcome["avg_net_return"])
            if bias == "FADE_FAVORED":
                ai_filter_returns.append(outcome["avg_net_return"])
                fade_total += 1
                if outcome["avg_net_return"] > 0:
                    fade_correct += 1
            elif bias == "CONTINUATION_FAVORED":
                cont_total += 1
                cont_ok = _continuation_correct(conn, int(r["event_id"]), r["direction"])
                if cont_ok is True:
                    cont_correct += 1
            elif bias == "WAIT_FOR_CONFIRMATION":
                wait_total += 1
                if outcome["avg_net_return"] <= 0:
                    wait_avoided_bad += 1
        elif bias == "WAIT_FOR_CONFIRMATION":
            pending = conn.execute(
                "SELECT confirmed_reversal FROM market_events_pending_shocks WHERE event_id = ?",
                (r["event_id"],),
            ).fetchone()
            if pending and not pending["confirmed_reversal"]:
                wait_total += 1
                wait_avoided_bad += 1

    lines.append("Bias distribution:")
    for b, n in sorted(bias_counts.items()):
        lines.append(f"  {b}: {n}")
    lines.append("")
    lines.append(f"Confidence buckets: {dict(conf_buckets)}")
    lines.append("")

    if fade_total >= 3:
        lines.append(
            f"fade_accuracy: {fade_correct}/{fade_total} "
            f"({100 * fade_correct / fade_total:.0f}%)",
        )
    else:
        lines.append("fade_accuracy: insufficient N")

    if cont_total >= 3:
        lines.append(
            f"continuation_accuracy: {cont_correct}/{cont_total} "
            f"({100 * cont_correct / cont_total:.0f}%)",
        )
    else:
        lines.append("continuation_accuracy: insufficient N")

    if wait_total >= 3:
        lines.append(
            f"wait_avoided_bad_entry: {wait_avoided_bad}/{wait_total} "
            f"({100 * wait_avoided_bad / wait_total:.0f}%)",
        )
    else:
        lines.append("wait_avoided_bad_entry: insufficient N")

    lines.append("")
    if len(det_returns) >= 3:
        det_avg = sum(det_returns) / len(det_returns)
        lines.append(
            f"deterministic_baseline: n={len(det_returns)} avg_net_return={det_avg:.3f}%",
        )
    else:
        lines.append("deterministic_baseline: insufficient N")

    if len(ai_filter_returns) >= 3:
        ai_avg = sum(ai_filter_returns) / len(ai_filter_returns)
        lines.append(
            f"hypothetical_ai_fade_filter: n={len(ai_filter_returns)} "
            f"avg_net_return={ai_avg:.3f}%",
        )
    else:
        lines.append("hypothetical_ai_fade_filter: insufficient N")

    lines.append("")
    lines.append("AI shadow filter is NOT applied to live paper execution.")
    lines.append("Deterministic R1-R5 remains source of truth for paper entries.")
    return "\n".join(lines)
