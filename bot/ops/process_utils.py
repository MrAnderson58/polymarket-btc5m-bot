"""Process discovery and graceful stop — macOS/Linux compatible."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from bot.config import BASE_DIR
from bot.research.futures_agent.env_bootstrap import project_root

MAIN_BOT_CMD_MARKERS = ("-m bot.main",)
TELEGRAM_POLL_MARKERS = (
    "futures_agent telegram-poll",
    "-m bot.research.futures_agent telegram-poll",
)

EXCLUDE_CMD_SUBSTRINGS = (
    "pytest",
    "unittest",
    "test_ops",
    "grep",
    "pgrep",
    "prod-stop",
    "prod-start",
    "prod-restart",
    "prod-status",
    "start-all",
    "stop-all",
)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    command: str


def project_python() -> Path:
    venv = BASE_DIR / ".venv" / "bin" / "python"
    return venv if venv.is_file() else Path(os.environ.get("PYTHON", "python3"))


def logs_dir() -> Path:
    path = BASE_DIR / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ps_rows() -> list[tuple[int, str]]:
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[tuple[int, str]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            rows.append((int(parts[0]), parts[1]))
        except ValueError:
            continue
    return rows


def _matches(cmd: str, markers: tuple[str, ...]) -> bool:
    if any(x in cmd for x in EXCLUDE_CMD_SUBSTRINGS):
        return False
    return any(marker in cmd for marker in markers)


def find_main_bot_processes() -> list[ProcessInfo]:
    return [
        ProcessInfo(pid=pid, command=cmd)
        for pid, cmd in _ps_rows()
        if _matches(cmd, MAIN_BOT_CMD_MARKERS)
    ]


def find_telegram_poll_processes() -> list[ProcessInfo]:
    return [
        ProcessInfo(pid=pid, command=cmd)
        for pid, cmd in _ps_rows()
        if _matches(cmd, TELEGRAM_POLL_MARKERS)
    ]


def telegram_lock_path() -> Path:
    return project_root() / "data" / "futures_agent_telegram_poll.lock"


def assess_telegram_lock() -> str:
    """Mirror futures_agent telegram-diagnose lock assessment."""
    lock_path = telegram_lock_path()
    if not lock_path.exists():
        return "no_lock_file"
    try:
        fd = os.open(str(lock_path), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return "stale_lock_available"
        except BlockingIOError:
            return "lock_held_by_process"
        finally:
            os.close(fd)
    except OSError:
        return "lock_check_failed"


def remove_stale_telegram_lock() -> tuple[bool, str]:
    """Remove lock file only when no process holds flock."""
    status = assess_telegram_lock()
    if status == "lock_held_by_process":
        return False, "lock held by running process"
    if status in {"stale_lock_available", "no_lock_file"}:
        path = telegram_lock_path()
        if path.exists():
            path.unlink()
            return True, "removed stale lock file"
        return True, "no lock file present"
    return False, f"cannot remove lock: {status}"


def stop_processes(
    processes: list[ProcessInfo],
    *,
    label: str,
    term_timeout_sec: float = 30.0,
) -> tuple[bool, list[str]]:
    """SIGTERM then SIGKILL. Returns (success, log lines)."""
    lines: list[str] = []
    if not processes:
        lines.append(f"{label}: not running")
        return True, lines

    for proc in processes:
        lines.append(f"{label}: stopping PID {proc.pid}")
        lines.append(f"  cmd: {proc.command[:200]}")

    remaining = {p.pid for p in processes}
    for pid in list(remaining):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            remaining.discard(pid)
        except PermissionError:
            lines.append(f"  permission denied sending SIGTERM to {pid}")
            return False, lines

    deadline = time.monotonic() + term_timeout_sec
    while remaining and time.monotonic() < deadline:
        time.sleep(0.5)
        for pid in list(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.discard(pid)

    for pid in list(remaining):
        lines.append(f"  SIGKILL PID {pid}")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            remaining.discard(pid)
        except PermissionError:
            lines.append(f"  permission denied sending SIGKILL to {pid}")
            return False, lines

    time.sleep(0.5)
    still = [p for p in processes if _pid_alive(p.pid)]
    if still:
        lines.append(f"{label}: failed to stop PIDs {[p.pid for p in still]}")
        return False, lines
    lines.append(f"{label}: stopped")
    return True, lines


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def start_detached(
    *,
    module_args: list[str],
    log_name: str,
) -> tuple[int | None, str]:
    """Start process detached with stdout/stderr redirected to logs/."""
    python = project_python()
    log_path = logs_dir() / log_name
    cmd = [str(python), *module_args]
    with open(log_path, "a", encoding="utf-8") as log_fp:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
    return proc.pid, str(log_path)
