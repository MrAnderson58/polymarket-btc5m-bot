"""Read-only audit of production execution failures.

Usage:
  python -m bot.research.execution_failure_audit

Does NOT modify execution, trading logic, or database.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter
from datetime import datetime
from typing import Any

REPORT_WIDTH = 72
WINDOW_SECONDS = 300


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _parse_market_end_ts(slug: str) -> int | None:
    m = re.search(r"-(\d{10})$", slug)
    return int(m.group(1)) + WINDOW_SECONDS if m else None


def _classify_error(msg: str | None) -> str:
    if not msg:
        return "unknown"
    low = msg.lower()
    if "entry_failed" in low:
        return "entry_failed"
    if "no orderbook" in low or "404" in low:
        return "clob_404_no_orderbook"
    if "ssl" in low or "handshake" in low:
        return "ssl_timeout"
    if "timeout" in low or "timed out" in low:
        return "gamma_timeout"
    if "duplicate" in low or "idempotency" in low:
        return "duplicate"
    return "other"


def _strategy_attribution(strategy_version: str, strategy_name: str) -> str:
    sv = (strategy_version or "").lower()
    sn = (strategy_name or "").lower()
    if "bidi" in sn or "bidirectional" in sn:
        return "bidirectional_shadow_intent"
    if sv in ("v2", "v2.5", "v3") or "early" in sn or "reversion" in sn:
        return "early_reversion_live"
    if "shadow" in sn:
        return "paper_shadow"
    if sv == "dry_run" or "paper" in sv:
        return "paper_execution"
    return "other_live"


def audit_execution_failures(conn: sqlite3.Connection) -> dict[str, Any]:
    report: dict[str, Any] = {
        "tables_present": {},
        "entry_failed": {"count": 0, "by_strategy": {}, "by_market": {}, "samples": []},
        "clob_404": {"count": 0, "by_strategy": {}, "post_expiry": 0, "samples": []},
        "timeouts": {"gamma": 0, "ssl": 0, "samples": []},
        "er_counters": {},
        "health_events": {},
        "attribution": {},
        "severity_verdict": "",
    }

    for tbl in ("order_intents", "order_fill_audit", "er_health_events", "er_strategy_counters", "er_funnel_events"):
        report["tables_present"][tbl] = _table_exists(conn, tbl)

    # Failed order intents (real/paper execution path)
    if _table_exists(conn, "order_intents"):
        failed = conn.execute(
            """
            SELECT strategy_version, strategy_name, market_slug, side, token_id,
                   status, error_message, created_at
            FROM order_intents
            WHERE status = 'failed' OR error_message IS NOT NULL
            ORDER BY created_at DESC
            """
        ).fetchall()
        by_class: Counter[str] = Counter()
        by_strat: Counter[str] = Counter()
        by_market: Counter[str] = Counter()
        post_expiry = 0

        for row in failed:
            err = row["error_message"] or ""
            cls = _classify_error(err)
            by_class[cls] += 1
            attr = _strategy_attribution(row["strategy_version"], row["strategy_name"])
            by_strat[f"{attr}:{row['strategy_name']}"] += 1
            slug = row["market_slug"] or "UNKNOWN"
            by_market[slug] += 1

            end_ts = _parse_market_end_ts(slug)
            created_ts = None
            if row["created_at"]:
                try:
                    created_ts = int(datetime.fromisoformat(row["created_at"].replace("Z", "")).timestamp())
                except ValueError:
                    pass
            if end_ts and created_ts and created_ts > end_ts + 30:
                post_expiry += 1

            sample = {
                "strategy": row["strategy_name"],
                "market": slug,
                "class": cls,
                "error": (err or "")[:120],
                "attribution": attr,
            }
            if cls == "entry_failed" and len(report["entry_failed"]["samples"]) < 5:
                report["entry_failed"]["samples"].append(sample)
            if cls == "clob_404_no_orderbook" and len(report["clob_404"]["samples"]) < 5:
                report["clob_404"]["samples"].append(sample)
            if cls in ("gamma_timeout", "ssl_timeout") and len(report["timeouts"]["samples"]) < 5:
                report["timeouts"]["samples"].append(sample)

        report["entry_failed"]["count"] = by_class.get("entry_failed", 0)
        report["entry_failed"]["by_strategy"] = dict(by_strat.most_common(15))
        report["entry_failed"]["by_market"] = dict(by_market.most_common(10))
        report["clob_404"]["count"] = by_class.get("clob_404_no_orderbook", 0)
        report["clob_404"]["by_strategy"] = {
            k: v for k, v in by_strat.items() if "404" in k or True
        }
        report["clob_404"]["post_expiry"] = post_expiry
        report["timeouts"]["gamma"] = by_class.get("gamma_timeout", 0)
        report["timeouts"]["ssl"] = by_class.get("ssl_timeout", 0)
        report["failure_class_totals"] = dict(by_class)

    # ER strategy counters: entry_attempt vs entry_success
    if _table_exists(conn, "er_strategy_counters"):
        rows = conn.execute(
            """
            SELECT strategy_name, entry_attempt, entry_success,
                   blocked_unknown, blocked_other, blocked_live_mode
            FROM er_strategy_counters
            WHERE entry_attempt > entry_success
            ORDER BY (entry_attempt - entry_success) DESC
            LIMIT 20
            """
        ).fetchall()
        report["er_counters"]["failed_attempts"] = [
            {
                "strategy": r["strategy_name"],
                "attempts": r["entry_attempt"],
                "success": r["entry_success"],
                "gap": r["entry_attempt"] - r["entry_success"],
                "blocked_unknown": r["blocked_unknown"],
            }
            for r in rows
        ]

    # Health events
    if _table_exists(conn, "er_health_events"):
        events = conn.execute(
            """
            SELECT event_type, COUNT(*) AS n
            FROM er_health_events
            GROUP BY event_type
            ORDER BY n DESC
            """
        ).fetchall()
        report["health_events"] = {r["event_type"]: r["n"] for r in events}

    # Attribution summary for entry_failed
    ef = report["entry_failed"]["count"]
    c404 = report["clob_404"]["count"]
    if ef == 0 and c404 == 0:
        report["attribution"]["entry_failed"] = (
            "No entry_failed rows in order_intents — failures are ER live/paper path only, "
            "not bidirectional shadow."
        )
    else:
        report["attribution"]["entry_failed"] = (
            "entry_failed belongs to Early Reversion live/paper execution "
            "(attempt_entry_open failure), not bidirectional shadow."
        )

    if c404 > 0 and report["clob_404"]["post_expiry"] > c404 * 0.3:
        report["stale_token_hypothesis"] = (
            f"Evidence supports stale-token hypothesis: {report['clob_404']['post_expiry']}/{c404} "
            "CLOB 404 errors occurred after market window end."
        )
    elif c404 > 0:
        report["stale_token_hypothesis"] = (
            "CLOB 404 errors present but mostly within market window — may be quote polling, "
            "not necessarily missed exits."
        )
    else:
        report["stale_token_hypothesis"] = "No CLOB 404 patterns in order_intents."

    total_failures = ef + c404 + report["timeouts"]["gamma"] + report["timeouts"]["ssl"]
    if total_failures == 0:
        report["severity_verdict"] = "LOW — no recorded execution failures in DB"
    elif c404 > ef and report["clob_404"]["post_expiry"] > 0:
        report["severity_verdict"] = "MEDIUM — stale token queries after rollover likely harmless polling"
    elif ef > 10:
        report["severity_verdict"] = "HIGH — entry_failed gaps may have caused missed ER entries"
    else:
        report["severity_verdict"] = "LOW-MEDIUM — isolated failures, review samples"

    return report


def render_failure_audit(report: dict[str, Any]) -> str:
    lines = ["EXECUTION FAILURE AUDIT (READ-ONLY)", "=" * REPORT_WIDTH]
    lines.append(f"Severity: {report.get('severity_verdict', '?')}")
    lines.append("")
    lines.append("FAILURE COUNTS")
    lines.append(f"  entry_failed: {report['entry_failed']['count']}")
    lines.append(f"  CLOB 404 no orderbook: {report['clob_404']['count']}")
    lines.append(f"  post-expiry 404s: {report['clob_404']['post_expiry']}")
    lines.append(f"  gamma timeout: {report['timeouts']['gamma']}")
    lines.append(f"  SSL timeout: {report['timeouts']['ssl']}")

    if report.get("failure_class_totals"):
        lines.append("")
        lines.append("BY CLASS")
        for k, v in report["failure_class_totals"].items():
            lines.append(f"  {k}: {v}")

    lines.append("")
    lines.append("ATTRIBUTION")
    for k, v in report.get("attribution", {}).items():
        lines.append(f"  {k}: {v}")
    lines.append(f"  stale_token: {report.get('stale_token_hypothesis', '?')}")

    if report["entry_failed"]["samples"]:
        lines.append("")
        lines.append("ENTRY_FAILED SAMPLES")
        for s in report["entry_failed"]["samples"]:
            lines.append(f"  {s['strategy']} | {s['market']} | {s['error'][:80]}")

    if report.get("er_counters", {}).get("failed_attempts"):
        lines.append("")
        lines.append("ER ENTRY_ATTEMPT > SUCCESS")
        for r in report["er_counters"]["failed_attempts"][:5]:
            lines.append(f"  {r['strategy']}: gap={r['gap']} unknown={r['blocked_unknown']}")

    return "\n".join(lines)


def main() -> int:
    from bot.database import connect, init_db

    parser = argparse.ArgumentParser(description="Execution failure audit (read-only)")
    parser.parse_args()

    init_db()
    with connect() as conn:
        report = audit_execution_failures(conn)
    print(render_failure_audit(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
