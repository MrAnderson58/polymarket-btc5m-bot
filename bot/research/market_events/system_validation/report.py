"""Phase E.5.1 — unified health report formatter."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.system_validation.types import ValidationResult, status_rank


def format_health_report(results: list[ValidationResult], *, mode: str = "SYSTEM VALIDATION") -> str:
    lines = [
        "═" * 60,
        f"PHASE E.5.1 — {mode}",
        "Read-only on production DB; load test uses isolated temp DB",
        "═" * 60,
        "",
    ]

    sections = {
        "Concurrency & Isolation": [],
        "Database Health": [],
        "Dedupe & Alerts": [],
        "Restart Recovery": [],
        "Performance": [],
        "Load Test": [],
    }
    mapping = {
        "db_isolation": "Concurrency & Isolation",
        "telegram_poll_singleton": "Concurrency & Isolation",
        "ai_worker_connection_isolation": "Concurrency & Isolation",
        "process_compatibility": "Concurrency & Isolation",
        "db_integrity": "Database Health",
        "db_file_size": "Database Health",
        "table_growth": "Database Health",
        "stale_ai_jobs": "Database Health",
        "alert_dedupe_keys": "Dedupe & Alerts",
        "event_dedup_keys": "Dedupe & Alerts",
        "ai_job_dedupe": "Dedupe & Alerts",
        "digest_dedupe": "Dedupe & Alerts",
        "alert_functional_dedupe": "Dedupe & Alerts",
        "pending_restore": "Restart Recovery",
        "open_paper_restore": "Restart Recovery",
        "alert_log_persistence": "Restart Recovery",
        "processing_benchmark": "Performance",
        "synthetic_load_test": "Load Test",
    }

    for r in results:
        sec = mapping.get(r.name, "Other")
        if sec not in sections:
            sections[sec] = []
        sections[sec].append(r)

    worst = "PASS"
    for sec_name, items in sections.items():
        if not items:
            continue
        lines.append(f"── {sec_name} ──")
        for r in items:
            icon = {"PASS": "✓", "WARN": "!", "FAIL": "✗", "SKIP": "○"}.get(r.status, "?")
            lines.append(f"  [{icon} {r.status}] {r.name}: {r.detail}")
            if status_rank(r.status) > status_rank(worst):
                worst = r.status
        lines.append("")

    counts = {s: sum(1 for r in results if r.status == s) for s in ("PASS", "WARN", "FAIL", "SKIP")}
    lines.extend([
        "── Summary ──",
        f"  PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']} SKIP={counts['SKIP']}",
        f"  VERDICT: {worst}",
        "",
        "Modes: PRODUCTION ONLINE | PAPER ONLINE | SHADOW | HISTORICAL REPLAY",
    ])
    return "\n".join(lines)


def report_to_json(results: list[ValidationResult]) -> str:
    payload: dict[str, Any] = {
        "phase": "E.5.1",
        "results": [
            {"name": r.name, "status": r.status, "detail": r.detail, "metrics": r.metrics}
            for r in results
        ],
        "verdict": max((r.status for r in results), key=status_rank, default="PASS"),
    }
    return json.dumps(payload, indent=2)
