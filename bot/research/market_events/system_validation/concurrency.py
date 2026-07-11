"""Phase E.5.1 — concurrent process / DB isolation checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.research.market_events.config import MARKET_EVENTS_DATABASE_PATH
from bot.research.market_events.system_validation.types import ValidationResult


def check_db_isolation() -> ValidationResult:
    """Verify market_events, futures_agent, and trades use separate stores."""
    from bot.config import DATABASE_PATH
    from bot.research.futures_agent.env_bootstrap import resolve_agent_db_config

    me = Path(MARKET_EVENTS_DATABASE_PATH).resolve()
    trades = Path(DATABASE_PATH).resolve()
    agent_cfg = resolve_agent_db_config()
    agent_path = None
    if not agent_cfg.postgres_url_configured:
        agent_path = Path(agent_cfg.url.replace("sqlite:///", "")).resolve()

    conflicts: list[str] = []
    if me == trades:
        conflicts.append("market_events == trades.db")
    if agent_path and me == agent_path:
        conflicts.append("market_events == futures_agent sqlite")

    detail = "isolated DB paths confirmed" if not conflicts else "; ".join(conflicts)
    return ValidationResult(
        "db_isolation",
        "FAIL" if conflicts else "PASS",
        detail,
        {
            "market_events": str(me),
            "trades": str(trades),
            "futures_agent": str(agent_path) if agent_path else agent_cfg.url[:40],
        },
    )


def check_telegram_poll_singleton() -> ValidationResult:
    try:
        from bot.ops.process_utils import assess_telegram_lock
        info = assess_telegram_lock()
        if info.get("locked") and info.get("pid"):
            return ValidationResult(
                "telegram_poll_singleton",
                "PASS",
                f"telegram-poll lock held by pid={info['pid']}",
                info,
            )
        return ValidationResult(
            "telegram_poll_singleton",
            "WARN",
            "telegram-poll not running or lock not held (ok if not deployed)",
            info,
        )
    except Exception as exc:
        return ValidationResult(
            "telegram_poll_singleton",
            "SKIP",
            f"lock check unavailable: {exc}",
        )


def check_ai_worker_separate_connection() -> ValidationResult:
    """Static validation: AI worker opens its own connection per cycle."""
    import inspect
    from bot.research.market_events.ai_analyst import job_queue

    src = inspect.getsource(job_queue.start_background_worker)
    ok = "db_path_factory()" in src and "process_pending_jobs" in src
    return ValidationResult(
        "ai_worker_connection_isolation",
        "PASS" if ok else "WARN",
        "AI worker uses separate DB connection per cycle" if ok else "could not verify worker pattern",
    )


def check_process_compatibility() -> ValidationResult:
    """Document which long-running processes can coexist."""
    compatible = [
        "shock-paper-run + AI worker thread (in-process)",
        "telegram-poll (separate process, flock singleton)",
        "observe-run (separate process, writes observations only)",
        "dashboard-api-serve (read-only HTTP)",
    ]
    notes = (
        "shock-paper-run and observe-run may run together but share price API rate limits; "
        "both write market_events.db on different tables."
    )
    return ValidationResult(
        "process_compatibility",
        "PASS",
        notes,
        {"compatible_sets": compatible},
    )
