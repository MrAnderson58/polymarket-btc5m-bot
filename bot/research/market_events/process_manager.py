"""Phase E.5.2 / FIX-4 — unified start-all / stop-all / status process supervisor.

Manages all market-events research services including telegram-poll.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.ops.process_utils import (
    EXCLUDE_CMD_SUBSTRINGS,
    ProcessInfo,
    find_telegram_poll_processes,
    logs_dir,
    project_python,
    remove_stale_telegram_lock,
    stop_processes,
    _ps_rows,
)
from bot.research.market_events.alert_engine.config import DASHBOARD_API_HOST, DASHBOARD_API_PORT
from bot.research.market_events.config import BASE_DIR, MARKET_EVENTS_DATABASE_PATH

PID_DIR = BASE_DIR / "data" / "market_events_supervisor"


@dataclass(frozen=True)
class ManagedService:
    key: str
    label: str
    module_args: tuple[str, ...]
    log_name: str
    markers: tuple[str, ...]
    env_overrides: dict[str, str] = field(default_factory=dict)


SERVICES: tuple[ManagedService, ...] = (
    ManagedService(
        key="shock-paper-core",
        label="shock-paper core",
        module_args=(
            "-m", "bot.research.market_events", "shock-paper-run",
            "--universe", "core", "--paper-only",
        ),
        log_name="me-shock-paper-core.log",
        markers=("bot.research.market_events shock-paper-run", "--universe core"),
        env_overrides={"ME_AI_EMBEDDED_IN_PAPER_RUN": "false"},
    ),
    ManagedService(
        key="shock-paper-tradfi",
        label="shock-paper tradfi",
        module_args=(
            "-m", "bot.research.market_events", "shock-paper-run",
            "--universe", "tradfi-liquid", "--paper-only",
        ),
        log_name="me-shock-paper-tradfi.log",
        markers=("bot.research.market_events shock-paper-run", "--universe tradfi-liquid"),
        env_overrides={"ME_AI_EMBEDDED_IN_PAPER_RUN": "false"},
    ),
    ManagedService(
        key="observe",
        label="observe",
        module_args=(
            "-m", "bot.research.market_events", "observe-run",
            "--universe", "tradfi-observe",
        ),
        log_name="me-observe.log",
        markers=("bot.research.market_events observe-run", "--universe tradfi-observe"),
    ),
    ManagedService(
        key="ai-worker",
        label="ai",
        module_args=("-m", "bot.research.market_events", "ai-worker-run"),
        log_name="me-ai-worker.log",
        markers=("bot.research.market_events ai-worker-run",),
    ),
    ManagedService(
        key="g3-live",
        label="g3",
        module_args=("-m", "bot.research.market_events", "g3-run"),
        log_name="me-g3-live.log",
        markers=("bot.research.market_events g3-run",),
    ),
    ManagedService(
        key="learning",
        label="learning",
        module_args=("-m", "bot.research.market_events", "learning-worker"),
        log_name="me-learning-worker.log",
        markers=("bot.research.market_events learning-worker",),
    ),
    ManagedService(
        key="narrative-engine",
        label="narrative-engine",
        module_args=("-m", "bot.research.market_events", "narrative-engine-run"),
        log_name="narrative-engine.log",
        markers=("bot.research.market_events narrative-engine-run",),
    ),
    ManagedService(
        key="dashboard",
        label="dashboard",
        module_args=("-m", "bot.research.market_events", "dashboard-api-serve"),
        log_name="me-dashboard.log",
        markers=("bot.research.market_events dashboard-api-serve",),
    ),
    ManagedService(
        key="telegram",
        label="telegram",
        module_args=("-m", "bot.research.futures_agent", "telegram-poll"),
        log_name="me-telegram.log",
        markers=("bot.research.futures_agent", "telegram-poll"),
    ),
)

START_ORDER = SERVICES
STOP_ORDER = tuple(reversed(SERVICES))

_SUPERVISOR_EXCLUDES = ("start-all", "stop-all", " system-validation")


def _pid_path(key: str) -> Path:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    return PID_DIR / f"{key}.pid"


def _meta_path(key: str) -> Path:
    return PID_DIR / f"{key}.json"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _matches(cmd: str, markers: tuple[str, ...]) -> bool:
    if any(x in cmd for x in EXCLUDE_CMD_SUBSTRINGS):
        return False
    if any(x in cmd for x in _SUPERVISOR_EXCLUDES):
        return False
    return all(m in cmd for m in markers)


def find_service_processes(svc: ManagedService) -> list[ProcessInfo]:
    # Telegram: use the same discovery as ops/prod_control so start/status agree.
    if svc.key == "telegram":
        found = find_telegram_poll_processes()
        if found:
            return found
    else:
        found = [
            ProcessInfo(pid=pid, command=cmd)
            for pid, cmd in _ps_rows()
            if _matches(cmd, svc.markers)
        ]
        if found:
            return found

    pid_file = _pid_path(svc.key)
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _pid_alive(pid):
                # Re-validate telegram pid file against live poll markers.
                if svc.key == "telegram":
                    live = find_telegram_poll_processes()
                    if any(p.pid == pid for p in live):
                        return live
                    # Stale pid for unrelated process — ignore.
                    return live
                return [ProcessInfo(pid=pid, command=f"(pid file) {svc.key}")]
        except ValueError:
            pass
    return []


def _write_pid(svc: ManagedService, pid: int, log_path: str) -> None:
    _pid_path(svc.key).write_text(str(pid))
    _meta_path(svc.key).write_text(json.dumps({
        "pid": pid, "log": log_path, "started_at": int(time.time()),
        "command": list(svc.module_args),
    }))


def _clear_pid(svc: ManagedService) -> None:
    for p in (_pid_path(svc.key), _meta_path(svc.key)):
        if p.exists():
            p.unlink()


def start_service(svc: ManagedService) -> tuple[bool, str]:
    existing = find_service_processes(svc)
    if existing:
        return False, f"{svc.label}: already running (PID {existing[0].pid})"

    notes: list[str] = []
    if svc.key == "telegram":
        try:
            from bot.research.futures_agent.env_bootstrap import bootstrap_config
            bootstrap_config()
        except Exception as exc:
            notes.append(f"bootstrap skipped: {exc}")
        removed, lock_msg = remove_stale_telegram_lock()
        if removed and lock_msg != "no lock file present":
            notes.append(f"lock: {lock_msg}")

    env = os.environ.copy()
    env.update(svc.env_overrides)
    log_path = logs_dir() / svc.log_name
    cmd = [str(project_python()), *svc.module_args]
    with open(log_path, "a", encoding="utf-8") as log_fp:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    _write_pid(svc, proc.pid, str(log_path))
    # Brief settle: catch immediate config/import crashes (e.g. missing TELEGRAM_BOT_TOKEN).
    time.sleep(0.6)
    rc = proc.poll()
    if rc is not None:
        _clear_pid(svc)
        tail = ""
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
            tail = " | ".join(ln.strip() for ln in text.strip().splitlines()[-3:] if ln.strip())
        except Exception:
            pass
        detail = f"exit={rc}"
        if tail:
            detail = f"{detail}; {tail[:240]}"
        msg = f"{svc.label}: exited immediately ({detail})"
        if notes:
            msg = f"{msg} ({'; '.join(notes)})"
        return False, msg

    msg = f"{svc.label}: started PID {proc.pid} log={log_path}"
    if notes:
        msg = f"{msg} ({'; '.join(notes)})"
    return True, msg


def stop_service(svc: ManagedService) -> tuple[bool, str]:
    procs = find_service_processes(svc)
    if not procs:
        _clear_pid(svc)
        if svc.key == "telegram":
            remove_stale_telegram_lock()
        return True, f"{svc.label}: not running"
    ok, lines = stop_processes(procs, label=svc.label)
    _clear_pid(svc)
    if svc.key == "telegram":
        removed, lock_msg = remove_stale_telegram_lock()
        if removed and "removed" in lock_msg:
            lines.append(f"telegram lock: {lock_msg}")
    summary = lines[-1] if lines else f"{svc.label}: stopped"
    return ok, summary


def start_all() -> list[str]:
    lines: list[str] = ["Starting market events supervisor...", ""]
    try:
        from bot.research.market_events.db import ensure_db_initialized, market_events_connection
        from bot.research.market_events.db_config import resolve_market_events_db_config
        from bot.research.market_events.event_schema import apply_migrations

        cfg = resolve_market_events_db_config()
        mode = ensure_db_initialized(cfg)
        lines.append(f"✓ database {cfg.backend} ready ({mode})")
        with market_events_connection() as conn:
            apply_migrations(conn)
        lines.append("✓ migrations up to date")
        lines.append("")
    except Exception as exc:
        lines.append(f"✗ database init failed: {exc}")
        lines.append("")
        return lines

    for svc in START_ORDER:
        ok, msg = start_service(svc)
        prefix = "✓" if ok else "✗"
        lines.append(f"{prefix} {msg}")
    lines.extend(["", f"logs: {logs_dir()}", f"pid dir: {PID_DIR}"])
    return lines


def stop_all() -> list[str]:
    lines: list[str] = ["Stopping market events supervisor...", ""]
    for svc in STOP_ORDER:
        ok, msg = stop_service(svc)
        prefix = "✓" if ok else "✗"
        lines.append(f"{prefix} {msg}")
    lines.append("")
    return lines


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def _health_block(conn: Any) -> list[str]:
    lines: list[str] = []
    row = conn.execute("PRAGMA quick_check").fetchone()
    db_ok = row and row[0] == "ok"
    lines.append(f"DB: {'OK' if db_ok else 'FAIL'}")

    from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
        heartbeat_status_line,
    )
    lines.append(heartbeat_status_line(conn))

    last = conn.execute("SELECT MAX(event_ts) AS t FROM market_events").fetchone()
    if last and last["t"]:
        age = int(time.time()) - int(last["t"])
        lines.append(f"Last event: {_format_age(age)}")
    else:
        lines.append("Last event: none")

    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_analysis_jobs WHERE status = 'pending'",
    ).fetchone()
    lines.append(f"AI queue pending: {int(pending['n'] if pending else 0)}")
    lines.append(f"Dashboard: http://{DASHBOARD_API_HOST}:{DASHBOARD_API_PORT}")
    return lines


def status_report() -> str:
    lines = ["MARKET EVENTS STATUS", ""]

    for svc in SERVICES:
        procs = find_service_processes(svc)
        if procs:
            lines.append(f"✓ {svc.label}  (PID {procs[0].pid})")
        else:
            lines.append(f"✗ {svc.label}")

    lines.append("")
    try:
        from bot.research.market_events.db import market_events_readonly_connection
        with market_events_readonly_connection() as conn:
            lines.extend(_health_block(conn))
    except Exception as exc:
        lines.append(f"DB: unreachable ({exc})")

    lines.extend(["", f"logs: {logs_dir()}", f"DB path: {MARKET_EVENTS_DATABASE_PATH}"])
    return "\n".join(lines)


def _service_by_key(key: str) -> ManagedService:
    for svc in SERVICES:
        if svc.key == key:
            return svc
    raise KeyError(key)


def restart_telegram() -> list[str]:
    """Stop then start telegram only — no full supervisor restart."""
    svc = _service_by_key("telegram")
    lines = ["Restarting telegram...", ""]
    ok_stop, msg_stop = stop_service(svc)
    lines.append(f"{'✓' if ok_stop else '✗'} stop: {msg_stop}")
    time.sleep(0.8)
    ok_start, msg_start = start_service(svc)
    lines.append(f"{'✓' if ok_start else '✗'} start: {msg_start}")
    return lines


def telegram_status_report() -> str:
    """Detailed telegram poll status (same process discovery as start/status)."""
    from bot.ops.process_utils import assess_telegram_lock, telegram_lock_path

    svc = _service_by_key("telegram")
    procs = find_service_processes(svc)
    running = bool(procs)
    lines = [
        "Telegram",
        "",
        f"Running: {'yes' if running else 'no'}",
    ]
    if procs:
        lines.append(f"PID: {procs[0].pid}")
        lines.append(f"Command: {procs[0].command[:120]}")
    else:
        lines.append("PID: —")

    lock = assess_telegram_lock()
    lines.extend([
        f"Lock: {lock}",
        f"Lock path: {telegram_lock_path()}",
        "Mode: polling",
    ])

    bot_ok = "—"
    try:
        from bot.research.futures_agent.telegram_config import get_telegram_bot_token
        from bot.research.futures_agent.telegram_intake_f52 import check_telegram_connected
        token = get_telegram_bot_token()
        bot_ok = "yes" if token and check_telegram_connected(token) else "no (token/getMe)"
    except Exception as exc:
        bot_ok = f"error ({exc})"
    lines.append(f"Bot connected: {bot_ok}")

    last_update = "—"
    last_command = "—"
    errors_today = 0
    messages_today = 0
    try:
        from datetime import datetime
        from bot.research.market_events.db import market_events_readonly_connection
        day_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        with market_events_readonly_connection() as conn:
            try:
                row = conn.execute(
                    """
                    SELECT MAX(created_at) AS t FROM market_events_inbound_trace_g04
                    """,
                ).fetchone()
                if row and row["t"]:
                    last_update = str(row["t"])
            except Exception:
                pass
            try:
                row = conn.execute(
                    """
                    SELECT command FROM market_events_inbound_trace_g04
                    WHERE command IS NOT NULL AND command != ''
                    ORDER BY created_at DESC LIMIT 1
                    """,
                ).fetchone()
                if row:
                    last_command = str(row["command"])
            except Exception:
                pass
            try:
                messages_today = int(conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM market_events_inbound_trace_g04
                    WHERE created_at >= ?
                    """,
                    (day_start,),
                ).fetchone()["n"] or 0)
            except Exception:
                pass
            try:
                errors_today = int(conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM market_events_inbound_trace_g04
                    WHERE created_at >= ? AND (error IS NOT NULL AND error != '')
                    """,
                    (day_start,),
                ).fetchone()["n"] or 0)
            except Exception:
                pass
    except Exception as exc:
        lines.append(f"DB probe: {exc}")

    lines.extend([
        f"Last update: {last_update}",
        f"Last command: {last_command}",
        f"Errors today: {errors_today}",
        f"Messages today: {messages_today}",
        f"Queue: n/a (polling)",
    ])

    log_path = logs_dir() / svc.log_name
    lines.append(f"Log: {log_path}")
    return "\n".join(lines)


def system_health_report() -> str:
    """BUG-S5.0.1 — one-shot health across DB/workers/telegram/learning/paper."""
    from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
        claude_call_allowed,
        s50_automatic_enabled,
        s50_telegram_only,
    )

    checks: list[tuple[str, bool, str]] = []
    lines = ["MARKET EVENTS HEALTH", ""]

    # DB
    try:
        from bot.research.market_events.db import market_events_readonly_connection
        from bot.research.market_events.db_config import resolve_market_events_db_config
        from bot.research.market_events.event_schema import SCHEMA_VERSION
        cfg = resolve_market_events_db_config()
        with market_events_readonly_connection() as conn:
            conn.execute("SELECT 1")
            max_v = None
            try:
                max_v = conn.execute(
                    "SELECT MAX(version) FROM market_events_migrations",
                ).fetchone()[0]
            except Exception:
                pass
            detail = f"{cfg.backend} schema={SCHEMA_VERSION} migrated={max_v}"
            ok = max_v is None or int(max_v) >= 52
            checks.append(("DB", ok, detail))
    except Exception as exc:
        checks.append(("DB", False, str(exc)))

    # Workers
    worker_bits: list[str] = []
    workers_ok = True
    for svc in SERVICES:
        procs = find_service_processes(svc)
        if procs:
            worker_bits.append(f"{svc.label}=PID{procs[0].pid}")
        else:
            worker_bits.append(f"{svc.label}=down")
            if svc.key in {"g3-live", "learning", "telegram", "dashboard"}:
                workers_ok = False
    checks.append(("Workers", workers_ok, "; ".join(worker_bits)))

    # Telegram
    tg_procs = find_service_processes(_service_by_key("telegram"))
    checks.append(("Telegram", bool(tg_procs), f"running={bool(tg_procs)}"))

    # Dashboard
    dash = find_service_processes(_service_by_key("dashboard"))
    checks.append(("Dashboard", bool(dash), f"http://{DASHBOARD_API_HOST}:{DASHBOARD_API_PORT}"))

    learning_detail = "—"
    paper_detail = "—"
    claude_detail = "—"
    pattern_detail = "—"
    decision_detail = "—"
    market_detail = "—"
    news_detail = "—"
    queue_detail = "—"
    errors_detail = "—"
    try:
        from bot.research.market_events.db import market_events_readonly_connection
        with market_events_readonly_connection() as conn:
            try:
                from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
                    learning_review_analytics_s40,
                    _count_pending_review_queue_s40,
                )
                an = learning_review_analytics_s40(conn)
                pending = _count_pending_review_queue_s40(conn)
                learning_detail = (
                    f"claude={an['claude_reviews']} local={an['local_reviews']} "
                    f"placeholder={an['placeholder_reviews']}"
                )
                queue_detail = f"pending_reviews={pending}"
                errors_detail = (
                    f"missing={an['missing_review']} worker_errors={an['worker_errors']}"
                )
                learning_ok = an["missing_review"] < 500 or an["local_reviews"] > 0
                checks.append(("Learning", learning_ok, learning_detail))
                checks.append(("Queue", pending < 2000, queue_detail))
                checks.append(("Errors", an["missing_review"] < 5000, errors_detail))
            except Exception as exc:
                checks.append(("Learning", False, str(exc)))
                checks.append(("Queue", False, str(exc)))
                checks.append(("Errors", False, str(exc)))

            try:
                from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
                    paper_performance_dashboard_s42,
                )
                paper = paper_performance_dashboard_s42(conn)
                paper_detail = (
                    f"equity=${paper.get('current_equity')} "
                    f"open={paper.get('open_trades')} closed={paper.get('closed_trades')}"
                )
                checks.append(("Paper", True, paper_detail))
            except Exception as exc:
                checks.append(("Paper", False, str(exc)))

            try:
                from bot.research.market_events.signal_intelligence.decision_engine_s20 import (
                    run_decision_engine_s20,
                )
                dec = run_decision_engine_s20(conn, "BTC", persist=False, force_fallback=True)
                decision_detail = str(dec.get("telegram") or "")[:80].replace("\n", " ")
                checks.append(("Decision", True, decision_detail or "ok"))
            except Exception as exc:
                checks.append(("Decision", False, str(exc)))

            try:
                from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
                    run_pattern_agent_s31,
                )
                pat = run_pattern_agent_s31(conn, symbol="BTC", timeframe="60m")
                pattern_detail = str(pat.get("pattern") or pat.get("label") or "ok")[:60]
                checks.append(("Pattern", True, pattern_detail))
            except Exception as exc:
                checks.append(("Pattern", False, str(exc)))

            try:
                from bot.research.market_events.signal_intelligence.news_collector_n11 import (
                    fetch_latest_news_n11,
                )
                news = fetch_latest_news_n11(conn, limit=1)
                news_detail = f"latest={len(news)}"
                checks.append(("News", True, news_detail))
            except Exception as exc:
                checks.append(("News", False, str(exc)))

            try:
                snap = conn.execute(
                    "SELECT MAX(created_at) AS t FROM market_snapshots_g3",
                ).fetchone()
                market_detail = f"last_snapshot={snap['t'] if snap else '—'}"
                checks.append(("Market", True, market_detail))
            except Exception as exc:
                checks.append(("Market", False, str(exc)))
    except Exception as exc:
        checks.append(("Learning", False, f"db {exc}"))

    allowed, reason = claude_call_allowed()
    claude_detail = (
        f"automatic={s50_automatic_enabled()} telegram_only={s50_telegram_only()} "
        f"auto_allowed={allowed}"
    )
    # Claude telegram-only is intentional PASS; failures only if misconfigured path crashes.
    checks.append(("Claude", True, claude_detail + (f" ({reason})" if reason else "")))

    overall = all(ok for _, ok, _ in checks)
    for name, ok, detail in checks:
        lines.append(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    lines.extend(["", f"Overall: {'PASS' if overall else 'FAIL'}"])
    return "\n".join(lines)
