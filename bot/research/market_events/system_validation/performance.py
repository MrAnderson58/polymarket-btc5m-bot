"""Phase E.5.1 — event and queue processing benchmarks."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.system_validation.types import ValidationResult


def benchmark_processing(conn: Any, *, n_events: int = 20) -> ValidationResult:
    from bot.research.market_events.ai_analyst.job_queue import enqueue_analysis_job, process_pending_jobs
    from bot.research.market_events.alert_engine.format_v2 import format_shock_alert_v2
    from unittest.mock import patch

    now = int(time.time())
    event_ids: list[int] = []

    t0 = time.perf_counter()
    for i in range(n_events):
        cur = conn.execute(
            """
            INSERT INTO market_events (
              event_ts, detected_ts, venue, symbol, direction, phase,
              trigger_window_seconds, return_pct, classification,
              detector_version, detector_triggers_json, dedup_key, created_at
            ) VALUES (?, ?, 'binance_futures', 'BEN', 'DOWN', 'MONITORING_REVERSAL',
              60, -1.5, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
            """,
            (now, now, f"bench-{now}-{i}", now),
        )
        event_ids.append(int(cur.lastrowid))
    insert_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    for eid in event_ids:
        format_shock_alert_v2(conn, eid)
    format_ms = (time.perf_counter() - t1) * 1000

    enqueue_ms = process_ms = 0.0
    processed = 0
    from unittest.mock import patch
    from bot.research.market_events.ai_analyst import config as ai_cfg

    orig = ai_cfg.AI_ENABLED
    ai_cfg.AI_ENABLED = True
    try:
        t2 = time.perf_counter()
        for eid in event_ids:
            enqueue_analysis_job(conn, event_id=eid)
        enqueue_ms = (time.perf_counter() - t2) * 1000

        from bot.research.market_events.ai_analyst.provider import DeterministicShadowProvider
        with patch(
            "bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider",
            return_value=DeterministicShadowProvider(),
        ):
            t3 = time.perf_counter()
            processed = process_pending_jobs(conn, max_jobs=n_events)
            process_ms = (time.perf_counter() - t3) * 1000
    finally:
        ai_cfg.AI_ENABLED = orig

    per_event = insert_ms / max(n_events, 1)
    status = "PASS" if per_event < 500 else "WARN"
    return ValidationResult(
        "processing_benchmark",
        status,
        f"{n_events} events: insert={insert_ms:.0f}ms format={format_ms:.0f}ms "
        f"enqueue={enqueue_ms:.0f}ms ai_process={process_ms:.0f}ms ({processed} jobs)",
        {
            "n_events": n_events,
            "insert_ms_total": round(insert_ms, 1),
            "format_ms_total": round(format_ms, 1),
            "enqueue_ms_total": round(enqueue_ms, 1),
            "ai_process_ms_total": round(process_ms, 1),
            "ai_jobs_processed": processed,
            "insert_ms_per_event": round(per_event, 2),
        },
    )
