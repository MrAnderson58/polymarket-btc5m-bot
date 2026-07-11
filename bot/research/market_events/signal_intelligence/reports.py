"""F.0 reports and weekly ranking."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.signal_intelligence.exchange_context import exchange_context_report
from bot.research.market_events.signal_intelligence.exhaustion import exhaustion_report
from bot.research.market_events.signal_intelligence.multitimeframe import multitimeframe_report


def signal_quality_report(conn: Any, *, days: int = 7) -> str:
    since = int(time.time()) - days * 86400
    lines = ["SIGNAL QUALITY REPORT (F.0)", ""]

    avg_score = conn.execute(
        "SELECT AVG(score) AS s FROM market_events_opportunity_scores_v2 o JOIN market_events e ON e.id = o.event_id WHERE e.event_ts >= ?",
        (since,),
    ).fetchone()
    lines.append(f"  Avg opportunity v2: {float(avg_score['s'] or 0):.1f}")

    ai_conf = conn.execute(
        "SELECT AVG(confidence) AS c FROM market_event_ai_analyses_f0 a JOIN market_events e ON e.id = a.event_id WHERE e.event_ts >= ?",
        (since,),
    ).fetchone()
    lines.append(f"  Avg AI confidence: {float(ai_conf['c'] or 0):.2f}")

    lat = conn.execute(
        """
        SELECT AVG(latency_ms) AS l FROM market_event_alert_log
        WHERE sent = 1 AND created_at >= ?
        """,
        (since,),
    ).fetchone()
    lines.append(f"  Avg alert latency: {float(lat['l'] or 0):.0f} ms")

    resolved = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_exchange_symbols WHERE status = 'RESOLVED'",
    ).fetchone()
    unsupported = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_exchange_symbols WHERE status = 'UNSUPPORTED_SYMBOL'",
    ).fetchone()
    lines.append(f"  Exchange resolved: {int(resolved['n'] if resolved else 0)}")
    lines.append(f"  Exchange unsupported: {int(unsupported['n'] if unsupported else 0)}")
    return "\n".join(lines)


def weekly_ranking_report(conn: Any) -> str:
    now = datetime.now(timezone.utc)
    period_key = now.strftime("%Y-W%W")
    since = int(time.time()) - 7 * 86400

    top_symbols = conn.execute(
        """
        SELECT e.symbol, COUNT(*) AS n, AVG(o.score) AS avg_score
        FROM market_events e
        LEFT JOIN market_events_opportunity_scores_v2 o ON o.event_id = e.id
        WHERE e.event_ts >= ?
        GROUP BY e.symbol ORDER BY n DESC, avg_score DESC LIMIT 10
        """,
        (since,),
    ).fetchall()

    top_exchanges = conn.execute(
        """
        SELECT resolved_venue AS venue, COUNT(*) AS n
        FROM market_event_exchange_symbols WHERE status = 'RESOLVED'
        GROUP BY resolved_venue ORDER BY n DESC
        """,
    ).fetchall()

    top_channels = conn.execute(
        """
        SELECT source, COUNT(*) AS n FROM market_event_context
        WHERE context_type IN ('TELEGRAM_SIGNAL', 'TRADER_THESIS')
          AND context_ts >= ?
        GROUP BY source ORDER BY n DESC LIMIT 10
        """,
        (since,),
    ).fetchall()

    strategies = conn.execute(
        """
        SELECT strategy, COUNT(*) AS n,
               AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) AS avg_pnl
        FROM paper_strategy_runs WHERE created_at >= ?
        GROUP BY strategy ORDER BY avg_pnl DESC
        """,
        (since,),
    ).fetchall()

    ranking = {
        "top_symbols": [dict(r) for r in top_symbols],
        "top_exchanges": [dict(r) for r in top_exchanges],
        "top_telegram_channels": [dict(r) for r in top_channels],
        "best_strategy": dict(strategies[0]) if strategies else None,
        "worst_strategy": dict(strategies[-1]) if strategies else None,
        "avg_latency_ms": conn.execute(
            "SELECT AVG(latency_ms) FROM market_event_alert_log WHERE sent=1 AND created_at>=?",
            (since,),
        ).fetchone()[0],
        "avg_score": conn.execute(
            "SELECT AVG(score) FROM market_events_opportunity_scores_v2 o JOIN market_events e ON e.id=o.event_id WHERE e.event_ts>=?",
            (since,),
        ).fetchone()[0],
        "avg_ai_confidence": conn.execute(
            "SELECT AVG(confidence) FROM market_event_ai_analyses_f0 a JOIN market_events e ON e.id=a.event_id WHERE e.event_ts>=?",
            (since,),
        ).fetchone()[0],
    }

    conn.execute(
        """
        INSERT OR REPLACE INTO market_events_signal_ranking_weekly (period_key, ranking_json, created_at)
        VALUES (?, ?, ?)
        """,
        (period_key, json.dumps(ranking, default=str), int(time.time())),
    )

    lines = [
        f"WEEKLY SIGNAL RANKING — {period_key}",
        "",
        "Top symbols:",
    ]
    for r in top_symbols[:5]:
        lines.append(f"  {r['symbol']} events={r['n']} avg_score={r['avg_score'] or 0:.1f}")
    lines.append("")
    lines.append("Top exchanges:")
    for r in top_exchanges:
        lines.append(f"  {r['venue']}: {r['n']}")
    lines.append("")
    lines.append("Top telegram channels:")
    for r in top_channels[:5]:
        lines.append(f"  {r['source']}: {r['n']}")
    if strategies:
        lines.extend([
            "",
            f"Best strategy: {strategies[0]['strategy']} avg_pnl={strategies[0]['avg_pnl'] or 0:.2f}%",
            f"Worst strategy: {strategies[-1]['strategy']} avg_pnl={strategies[-1]['avg_pnl'] or 0:.2f}%",
        ])
    lines.extend([
        "",
        f"Average latency: {ranking['avg_latency_ms'] or 0:.0f} ms",
        f"Average score: {ranking['avg_score'] or 0:.1f}",
        f"Average AI confidence: {ranking['avg_ai_confidence'] or 0:.2f}",
    ])
    return "\n".join(lines)


def run_all_f0_reports(conn: Any, *, days: int = 7) -> dict[str, str]:
    return {
        "multitimeframe": multitimeframe_report(conn, days=days),
        "exhaustion": exhaustion_report(conn, days=days),
        "exchange_context": exchange_context_report(conn, days=days),
        "signal_quality": signal_quality_report(conn, days=days),
        "weekly_ranking": weekly_ranking_report(conn),
    }
