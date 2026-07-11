"""Phase E.5.1 — run all system validation checks."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from bot.research.market_events.system_validation.concurrency import (
    check_ai_worker_separate_connection,
    check_db_isolation,
    check_process_compatibility,
    check_telegram_poll_singleton,
)
from bot.research.market_events.system_validation.db_health import (
    check_db_file_size,
    check_db_integrity,
    check_stale_jobs,
    check_table_growth,
)
from bot.research.market_events.system_validation.dedupe_audit import (
    check_ai_job_dedupe,
    check_alert_dedupe_keys,
    check_digest_dedupe,
    check_event_dedup_constraint,
    functional_alert_dedupe_test,
)
from bot.research.market_events.system_validation.load_test import run_synthetic_load
from bot.research.market_events.system_validation.performance import benchmark_processing
from bot.research.market_events.system_validation.restart_audit import (
    check_alert_log_survives_reconnect,
    check_open_paper_restore,
    check_pending_restore,
)
from bot.research.market_events.system_validation.types import ValidationResult


def run_system_validation(
    conn: Any,
    *,
    days: int = 7,
    benchmark_events: int = 20,
    load_events: int = 200,
    skip_load: bool = False,
    skip_mutating: bool = False,
    temp_db_path: Path | None = None,
) -> list[ValidationResult]:
    results: list[ValidationResult] = []

    # Read-only / external checks
    results.append(check_db_isolation())
    results.append(check_telegram_poll_singleton())
    results.append(check_ai_worker_separate_connection())
    results.append(check_process_compatibility())
    results.append(check_db_file_size())
    results.append(check_db_integrity(conn))
    results.append(check_table_growth(conn))
    results.append(check_stale_jobs(conn))
    results.append(check_alert_dedupe_keys(conn, days=days))
    results.append(check_event_dedup_constraint(conn))
    results.append(check_ai_job_dedupe(conn))
    results.append(check_digest_dedupe(conn))

    if skip_mutating:
        results.append(ValidationResult(
            "mutating_checks", "SKIP", "skipped on production (--read-only)",
        ))
    else:
        bench_path = temp_db_path
        if bench_path is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            bench_path = Path(tmp.name)
            tmp.close()
        from bot.research.market_events.db import market_events_connection
        from bot.research.market_events.event_schema import apply_migrations

        with market_events_connection(db_path=bench_path) as bench_conn:
            apply_migrations(bench_conn)
            results.append(check_pending_restore(bench_conn))
            results.append(check_open_paper_restore(bench_conn))
            results.append(functional_alert_dedupe_test(bench_conn))
            results.append(benchmark_processing(bench_conn, n_events=benchmark_events))

        results.append(check_alert_log_survives_reconnect(bench_path))

    if skip_load:
        results.append(ValidationResult("synthetic_load_test", "SKIP", "skipped (--skip-load)"))
    else:
        results.append(run_synthetic_load(n_events=load_events))

    return results
