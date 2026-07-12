"""Phase F.7.1 — operations CLI reports for F7 diagnostics."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any


def _today_bounds(now: int | None = None) -> tuple[int, int, str]:
    now = now or int(time.time())
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    start = int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return start, now, dt.strftime("%Y-%m-%d")


def _since_days(days: int) -> int:
    return int(time.time()) - days * 86400


def market_score_report(conn: Any, *, days: int = 7, limit: int = 30) -> str:
    since = _since_days(days)
    rows = conn.execute(
        """
        SELECT i.*, e.symbol, e.event_ts
        FROM market_events_market_intelligence_f7 i
        JOIN market_events e ON e.id = i.event_id
        WHERE i.created_at >= ?
        ORDER BY i.market_score DESC, i.created_at DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()
    avg = conn.execute(
        """
        SELECT AVG(market_score) AS s, AVG(final_confidence) AS c, COUNT(*) AS n
        FROM market_events_market_intelligence_f7 WHERE created_at >= ?
        """,
        (since,),
    ).fetchone()

    lines = [
        "MARKET SCORE REPORT (F.7.1)",
        f"period_days: {days}",
        f"records: {int(avg['n'] or 0)}",
        f"avg_market_score: {float(avg['s'] or 0):.1f}",
        f"avg_final_confidence: {float(avg['c'] or 0):.1f}",
        "",
    ]
    for r in rows:
        comps = json.loads(r["component_scores_json"] or "{}")
        top = sorted(comps.items(), key=lambda x: -x[1])[:3]
        comp_str = ", ".join(f"{k}={v:.0f}" for k, v in top)
        lines.append(
            f"  event={r['event_id']} {r['symbol']} score={r['market_score']:.0f} "
            f"conf={r['final_confidence']:.1f} [{comp_str}]"
        )
    if not rows:
        lines.append("  (no F7 records in period)")
    return "\n".join(lines)


def liquidation_report(conn: Any, *, days: int = 7, limit: int = 30) -> str:
    since = _since_days(days)
    rows = conn.execute(
        """
        SELECT i.event_id, i.liquidation_regime, i.liquidation_reversal_prob,
               i.liquidation_continuation_prob, i.liquidation_intel_json,
               e.symbol, i.created_at
        FROM market_events_market_intelligence_f7 i
        JOIN market_events e ON e.id = i.event_id
        WHERE i.created_at >= ?
        ORDER BY i.created_at DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()
    by_regime = conn.execute(
        """
        SELECT liquidation_regime, COUNT(*) AS n
        FROM market_events_market_intelligence_f7
        WHERE created_at >= ?
        GROUP BY liquidation_regime ORDER BY n DESC
        """,
        (since,),
    ).fetchall()

    lines = ["LIQUIDATION REPORT (F.7.1)", f"period_days: {days}", "", "By regime:"]
    for r in by_regime:
        lines.append(f"  {r['liquidation_regime'] or 'unknown'}: {r['n']}")
    lines.append("")
    for r in rows:
        intel = json.loads(r["liquidation_intel_json"] or "{}")
        lines.append(
            f"  {r['symbol']} event={r['event_id']} {r['liquidation_regime']} "
            f"rev={float(r['liquidation_reversal_prob'] or 0):.0%} "
            f"— {intel.get('summary', '')[:60]}"
        )
    if not rows:
        lines.append("  (no records)")
    return "\n".join(lines)


def whale_report(conn: Any, *, days: int = 7, limit: int = 30) -> str:
    since = _since_days(days)
    rows = conn.execute(
        """
        SELECT i.event_id, i.whale_score, i.whale_intel_json, e.symbol
        FROM market_events_market_intelligence_f7 i
        JOIN market_events e ON e.id = i.event_id
        WHERE i.created_at >= ?
        ORDER BY i.whale_score DESC, i.created_at DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()
    lines = ["WHALE ACTIVITY REPORT (F.7.1)", f"period_days: {days}", ""]
    for r in rows:
        intel = json.loads(r["whale_intel_json"] or "{}")
        lines.append(
            f"  {r['symbol']} event={r['event_id']} score={r['whale_score']:.0f} "
            f"— {intel.get('summary', '')}"
        )
    if not rows:
        lines.append("  (no records)")
    return "\n".join(lines)


def dominance_report(conn: Any, *, days: int = 7, limit: int = 30) -> str:
    since = _since_days(days)
    rows = conn.execute(
        """
        SELECT i.event_id, i.dominance_regime, i.dominance_json, e.symbol
        FROM market_events_market_intelligence_f7 i
        JOIN market_events e ON e.id = i.event_id
        WHERE i.created_at >= ?
        ORDER BY i.created_at DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()
    by_regime = conn.execute(
        """
        SELECT dominance_regime, COUNT(*) AS n
        FROM market_events_market_intelligence_f7
        WHERE created_at >= ?
        GROUP BY dominance_regime ORDER BY n DESC
        """,
        (since,),
    ).fetchall()

    lines = ["DOMINANCE REPORT (F.7.1)", f"period_days: {days}", "", "By regime:"]
    for r in by_regime:
        lines.append(f"  {r['dominance_regime'] or 'unknown'}: {r['n']}")
    lines.append("")
    for r in rows:
        dom = json.loads(r["dominance_json"] or "{}")
        lines.append(
            f"  {r['symbol']} {r['dominance_regime']} "
            f"BTC={dom.get('btc_return', 0):+.1f}% TOTAL3={dom.get('total3_return', 0):+.1f}%"
        )
    if not rows:
        lines.append("  (no records)")
    return "\n".join(lines)


def signal_trace_report(conn: Any, *, days: int = 1, limit: int = 50) -> str:
    since = _since_days(days)
    stage_stats = conn.execute(
        """
        SELECT stage, status, COUNT(*) AS n
        FROM market_events_signal_trace_f51
        WHERE created_at >= ?
        GROUP BY stage, status
        ORDER BY stage, status
        """,
        (since,),
    ).fetchall()
    failures = conn.execute(
        """
        SELECT t.event_id, t.stage, t.reason, e.symbol, t.created_at
        FROM market_events_signal_trace_f51 t
        JOIN market_events e ON e.id = t.event_id
        WHERE t.created_at >= ? AND t.status IN ('FAILED', 'not sent', 'SKIPPED')
        ORDER BY t.created_at DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()

    lines = ["SIGNAL TRACE REPORT (F.7.1)", f"period_days: {days}", "", "Stage summary:"]
    for r in stage_stats:
        lines.append(f"  {r['stage']}: {r['status']} × {r['n']}")
    lines.extend(["", "Recent failures / filtered:"])
    for r in failures:
        lines.append(
            f"  event={r['event_id']} {r['symbol']} [{r['stage']}] {r['reason'] or r['status']}"
        )
    if not failures:
        lines.append("  (none)")
    return "\n".join(lines)


def today_summary_report(conn: Any) -> str:
    start, _, day_key = _today_bounds()

    signals = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events WHERE event_ts >= ?",
        (start,),
    ).fetchone()["n"]

    f5_total = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_reports_f5 f
        JOIN market_events e ON e.id = f.event_id
        WHERE e.event_ts >= ?
        """,
        (start,),
    ).fetchone()["n"]

    telegram_sent = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_priority_f5
        WHERE telegram_sent = 1 AND created_at >= ?
        """,
        (start,),
    ).fetchone()["n"]
    if not telegram_sent:
        telegram_sent = conn.execute(
            """
            SELECT COUNT(*) AS n FROM market_event_alert_log
            WHERE sent = 1 AND alert_type = 'SHOCK' AND created_at >= ?
            """,
            (start,),
        ).fetchone()["n"]

    filtered = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_reports_f5 f
        JOIN market_events e ON e.id = f.event_id
        WHERE e.event_ts >= ? AND f.telegram_eligible = 0
        """,
        (start,),
    ).fetchone()["n"]
    if filtered == 0 and f5_total:
        filtered = max(0, int(f5_total) - int(telegram_sent))

    avg_conf = conn.execute(
        """
        SELECT AVG(i.final_confidence) AS c FROM market_events_market_intelligence_f7 i
        JOIN market_events e ON e.id = i.event_id
        WHERE e.event_ts >= ?
        """,
        (start,),
    ).fetchone()["c"]
    if avg_conf is None:
        avg_conf = conn.execute(
            """
            SELECT AVG(f.dynamic_confidence) AS c FROM market_events_signal_reports_f5 f
            JOIN market_events e ON e.id = f.event_id
            WHERE e.event_ts >= ?
            """,
            (start,),
        ).fetchone()["c"]

    avg_ms = conn.execute(
        """
        SELECT AVG(i.market_score) AS s FROM market_events_market_intelligence_f7 i
        JOIN market_events e ON e.id = i.event_id
        WHERE e.event_ts >= ?
        """,
        (start,),
    ).fetchone()["s"]

    top_sym = conn.execute(
        """
        SELECT e.symbol, COUNT(*) AS n FROM market_events e
        WHERE e.event_ts >= ? GROUP BY e.symbol ORDER BY n DESC LIMIT 1
        """,
        (start,),
    ).fetchone()

    best = conn.execute(
        """
        SELECT e.symbol, i.final_confidence, i.success_probability
        FROM market_events_market_intelligence_f7 i
        JOIN market_events e ON e.id = i.event_id
        WHERE e.event_ts >= ?
        ORDER BY i.final_confidence DESC, i.success_probability DESC
        LIMIT 1
        """,
        (start,),
    ).fetchone()

    worst = conn.execute(
        """
        SELECT e.symbol, i.final_confidence
        FROM market_events_market_intelligence_f7 i
        JOIN market_events e ON e.id = i.event_id
        WHERE e.event_ts >= ?
        ORDER BY i.final_confidence ASC
        LIMIT 1
        """,
        (start,),
    ).fetchone()

    lines = [
        f"TODAY — {day_key}",
        "",
        "Signals:",
        str(int(signals or 0)),
        "",
        "Telegram:",
        str(int(telegram_sent or 0)),
        "",
        "Filtered:",
        str(int(filtered or 0)),
        "",
        "Average confidence:",
        f"{float(avg_conf or 0):.1f}",
        "",
        "Average market score:",
        f"{int(round(float(avg_ms or 0)))}",
        "",
        "Top symbol:",
        str(top_sym["symbol"]) if top_sym else "—",
        "",
        "Best setup:",
        str(best["symbol"]) if best else "—",
        "",
        "Worst setup:",
        str(worst["symbol"]) if worst else "—",
    ]
    return "\n".join(lines)
