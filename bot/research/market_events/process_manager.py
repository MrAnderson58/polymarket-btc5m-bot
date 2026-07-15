"""Phase E.5.2 — start-all / stop-all / status process supervisor."""

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
        key="dashboard",
        label="dashboard",
        module_args=("-m", "bot.research.market_events", "dashboard-api-serve"),
        log_name="me-dashboard.log",
        markers=("bot.research.market_events dashboard-api-serve",),
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
    return True, f"{svc.label}: started PID {proc.pid} log={log_path}"


def stop_service(svc: ManagedService) -> tuple[bool, str]:
    procs = find_service_processes(svc)
    if not procs:
        _clear_pid(svc)
        return True, f"{svc.label}: not running"
    ok, lines = stop_processes(procs, label=svc.label)
    _clear_pid(svc)
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

    tg = find_telegram_poll_processes()
    if tg:
        lines.append(f"✓ telegram  (PID {tg[0].pid})")
    else:
        lines.append("✗ telegram")
        lines.append(
            "  note: not managed by market_events start-all; "
            "start via: python -m bot.ops.prod_control start  "
            "(futures_agent telegram-poll)"
        )

    lines.append("")
    try:
        from bot.research.market_events.db import market_events_readonly_connection
        with market_events_readonly_connection() as conn:
            lines.extend(_health_block(conn))
    except Exception as exc:
        lines.append(f"DB: unreachable ({exc})")

    lines.extend(["", f"logs: {logs_dir()}", f"DB path: {MARKET_EVENTS_DATABASE_PATH}"])
    return "\n".join(lines)
