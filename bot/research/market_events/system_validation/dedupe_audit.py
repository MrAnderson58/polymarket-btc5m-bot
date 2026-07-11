"""Phase E.5.1 — Telegram and event dedupe validation."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.system_validation.types import ValidationResult


def check_alert_dedupe_keys(conn: Any, *, days: int = 7) -> ValidationResult:
    since = int(time.time()) - days * 86400
    dupes = conn.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT dedupe_key FROM market_event_alert_log
          WHERE created_at >= ? GROUP BY dedupe_key HAVING COUNT(*) > 1
        )
        """,
        (since,),
    ).fetchone()
    n = int(dupes[0] if dupes else 0)
    status = "FAIL" if n else "PASS"
    return ValidationResult(
        "alert_dedupe_keys",
        status,
        f"duplicate dedupe_key groups in last {days}d: {n}",
        {"duplicate_groups": n, "days": days},
    )


def check_event_dedup_constraint(conn: Any) -> ValidationResult:
    dupes = conn.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT dedup_key FROM market_events
          WHERE dedup_key IS NOT NULL GROUP BY dedup_key HAVING COUNT(*) > 1
        )
        """,
    ).fetchone()
    n = int(dupes[0] if dupes else 0)
    status = "FAIL" if n else "PASS"
    return ValidationResult(
        "event_dedup_keys",
        status,
        f"duplicate market_events.dedup_key: {n}",
        {"duplicate_groups": n},
    )


def check_ai_job_dedupe(conn: Any) -> ValidationResult:
    dupes = conn.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT event_id, prompt_version FROM market_event_analysis_jobs
          WHERE status IN ('pending', 'running')
          GROUP BY event_id, prompt_version HAVING COUNT(*) > 1
        )
        """,
    ).fetchone()
    n = int(dupes[0] if dupes else 0)
    status = "FAIL" if n else "PASS"
    return ValidationResult(
        "ai_job_dedupe",
        status,
        f"duplicate pending/running AI jobs per event: {n}",
        {"duplicate_groups": n},
    )


def check_digest_dedupe(conn: Any) -> ValidationResult:
    dupes = conn.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT digest_type, period_key FROM market_events_digest_log
          GROUP BY digest_type, period_key HAVING COUNT(*) > 1
        )
        """,
    ).fetchone()
    n = int(dupes[0] if dupes else 0)
    status = "FAIL" if n else "PASS"
    return ValidationResult(
        "digest_dedupe",
        status,
        f"duplicate digest log entries: {n}",
        {"duplicate_groups": n},
    )


def functional_alert_dedupe_test(conn: Any) -> ValidationResult:
    """Send path dedupe — second call must not insert."""
    from unittest.mock import patch

    from bot.research.market_events.market_event_alerts import alert_shock_detected

    cur = conn.execute(
        """
        INSERT INTO market_events (
          event_ts, detected_ts, venue, symbol, direction, phase,
          trigger_window_seconds, return_pct, classification,
          detector_version, detector_triggers_json, dedup_key, created_at
        ) VALUES (?, ?, 'binance_futures', 'VAL', 'DOWN', 'MONITORING_REVERSAL',
          60, -2.0, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
        """,
        (int(time.time()), int(time.time()), f"dedup-val-{time.time_ns()}", int(time.time())),
    )
    eid = int(cur.lastrowid)
    before = conn.execute(
        "SELECT COUNT(*) FROM market_event_alert_log WHERE event_id = ?",
        (eid,),
    ).fetchone()[0]
    with patch("bot.research.market_events.market_event_alerts._send_telegram", return_value=(True, None)):
        with patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True):
            with patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True):
                alert_shock_detected(conn, int(eid))
                alert_shock_detected(conn, int(eid))
    after = conn.execute(
        "SELECT COUNT(*) FROM market_event_alert_log WHERE event_id = ?",
        (eid,),
    ).fetchone()[0]
    inserted = after - before
    status = "PASS" if inserted == 1 else "FAIL"
    return ValidationResult(
        "alert_functional_dedupe",
        status,
        f"alert log rows added on double-send: {inserted} (expected 1)",
        {"rows_added": inserted},
    )
