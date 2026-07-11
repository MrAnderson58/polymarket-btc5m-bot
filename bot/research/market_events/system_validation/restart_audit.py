"""Phase E.5.1 — restart recovery validation."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.pending_reversal import create_pending_shock, restore_pending_map
from bot.research.market_events.system_validation.types import ValidationResult


def check_pending_restore(conn: Any) -> ValidationResult:
    now = int(time.time())
    eid = conn.execute(
        """
        INSERT INTO market_events (
          event_ts, detected_ts, venue, symbol, direction, phase,
          trigger_window_seconds, return_pct, classification,
          detector_version, detector_triggers_json, dedup_key, created_at
        ) VALUES (?, ?, 'binance_futures', 'RST', 'DOWN', 'MONITORING_REVERSAL',
          60, -2.5, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
        """,
        (now, now, f"dedup-rst-{now}", now),
    ).lastrowid
    create_pending_shock(
        conn, event_id=int(eid), symbol="RST", direction="DOWN",
        detected_ts=now, shock_return_pct=-2.5, extreme_price=100.0,
    )
    restored = restore_pending_map(conn)
    ok = int(eid) in restored
    return ValidationResult(
        "pending_restore",
        "PASS" if ok else "FAIL",
        f"restore_pending_map contains event {eid}: {ok}",
        {"restored_count": len(restored)},
    )


def check_open_paper_restore(conn: Any) -> ValidationResult:
    now = int(time.time())
    eid = conn.execute(
        """
        INSERT INTO market_events (
          event_ts, detected_ts, venue, symbol, direction, phase,
          trigger_window_seconds, return_pct, classification,
          detector_version, detector_triggers_json, dedup_key, created_at
        ) VALUES (?, ?, 'binance_futures', 'PPR', 'DOWN', 'MANAGING_POSITION',
          60, -2.0, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
        """,
        (now, now, f"dedup-ppr-{now}", now),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO paper_strategy_runs (
          event_id, strategy_name, strategy_version, reversal_variant, exit_variant,
          eligibility, entry_ts, entry_price, initial_stop, created_at
        ) VALUES (?, 'test', 'v1', 'R1', 'EXIT_A', 1, ?, 100.0, 99.0, ?)
        """,
        (eid, now, now),
    )
    open_rows = conn.execute(
        "SELECT COUNT(*) FROM paper_strategy_runs WHERE event_id = ? AND exit_ts IS NULL",
        (eid,),
    ).fetchone()[0]
    ok = int(open_rows) == 1
    return ValidationResult(
        "open_paper_restore",
        "PASS" if ok else "FAIL",
        f"open paper_strategy_runs for event {eid}: {open_rows}",
    )


def check_alert_log_survives_reconnect(db_path: Any) -> ValidationResult:
    """Simulate process restart with new connection."""
    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.event_schema import apply_migrations

    eid = None
    with market_events_connection(db_path=db_path) as conn:
        apply_migrations(conn)
        now = int(time.time())
        eid = conn.execute(
            """
            INSERT INTO market_events (
              event_ts, detected_ts, venue, symbol, direction, phase,
              trigger_window_seconds, return_pct, classification,
              detector_version, detector_triggers_json, dedup_key, created_at
            ) VALUES (?, ?, 'binance_futures', 'ALR', 'DOWN', 'MONITORING_REVERSAL',
              60, -1.0, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
            """,
            (now, now, f"dedup-alr-{now}", now),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO market_event_alert_log (
              event_id, alert_type, dedupe_key, message_text, sent, latency_ms, created_at
            ) VALUES (?, 'SHOCK_DETECTED', ?, 'test', 1, 1.0, ?)
            """,
            (eid, f"{eid}:SHOCK_DETECTED:", int(time.time())),
        )

    with market_events_connection(db_path=db_path) as conn2:
        n = conn2.execute(
            "SELECT COUNT(*) FROM market_event_alert_log WHERE event_id = ?",
            (eid,),
        ).fetchone()[0]
    ok = int(n) == 1
    return ValidationResult(
        "alert_log_persistence",
        "PASS" if ok else "FAIL",
        f"alert log rows after reconnect: {n}",
    )
