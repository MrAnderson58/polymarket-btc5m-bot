"""Phase E.5.1 — synthetic load test (isolated temp DB only)."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.system_validation.types import ValidationResult


def run_synthetic_load(*, n_events: int = 200) -> ValidationResult:
    """Inject artificial events into temp DB; never touches production."""
    from unittest.mock import patch

    from bot.research.market_events.ai_analyst.job_queue import enqueue_analysis_job, process_pending_jobs
    from bot.research.market_events.market_event_alerts import alert_shock_detected

    if n_events > 2000:
        n_events = 2000

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "load_test.db"
        now = int(time.time())
        t0 = time.perf_counter()

        with market_events_connection(db_path=db_path) as conn:
            apply_migrations(conn)
            for i in range(n_events):
                conn.execute(
                    """
                    INSERT INTO market_events (
                      event_ts, detected_ts, venue, symbol, direction, phase,
                      trigger_window_seconds, return_pct, classification,
                      detector_version, detector_triggers_json, dedup_key, created_at
                    ) VALUES (?, ?, 'binance_futures', 'LD', 'DOWN', 'MONITORING_REVERSAL',
                      60, -2.0, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
                    """,
                    (now, now, f"load-{now}-{i}", now),
                )

            event_ids = [
                int(r[0]) for r in conn.execute("SELECT id FROM market_events ORDER BY id DESC LIMIT ?", (n_events,)).fetchall()
            ]

            alerts_sent = 0
            with patch("bot.research.market_events.market_event_alerts._send_telegram", return_value=(True, None)):
                with patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True):
                    with patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True):
                        for eid in event_ids[: min(50, n_events)]:
                            if alert_shock_detected(conn, eid):
                                alerts_sent += 1
                            alert_shock_detected(conn, eid)

            dupes = conn.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT dedupe_key FROM market_event_alert_log GROUP BY dedupe_key HAVING COUNT(*) > 1
                )
                """,
            ).fetchone()[0]

            from bot.research.market_events.ai_analyst import config as ai_cfg
            orig = ai_cfg.AI_ENABLED
            ai_cfg.AI_ENABLED = True
            try:
                for eid in event_ids:
                    enqueue_analysis_job(conn, event_id=eid)
                from bot.research.market_events.ai_analyst.provider import DeterministicShadowProvider
                with patch(
                    "bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider",
                    return_value=DeterministicShadowProvider(),
                ):
                    processed = process_pending_jobs(conn, max_jobs=n_events)
            finally:
                ai_cfg.AI_ENABLED = orig

        elapsed = time.perf_counter() - t0
        eps = n_events / elapsed if elapsed > 0 else 0
        status = "PASS" if int(dupes) == 0 and processed >= n_events * 0.9 else "WARN"

        return ValidationResult(
            "synthetic_load_test",
            status,
            f"{n_events} events in {elapsed:.2f}s ({eps:.0f} evt/s); "
            f"alerts={alerts_sent} dupes={dupes} ai_jobs={processed}",
            {
                "n_events": n_events,
                "elapsed_sec": round(elapsed, 3),
                "events_per_sec": round(eps, 1),
                "alerts_first_send": alerts_sent,
                "alert_dedupe_violations": int(dupes),
                "ai_jobs_processed": processed,
                "isolated_db": True,
            },
        )
