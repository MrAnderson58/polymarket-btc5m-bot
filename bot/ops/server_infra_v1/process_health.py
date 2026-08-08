"""Real-process health helpers — never trust stale pidfiles alone."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bot.ops.process_utils import ProcessInfo, _ps_rows, find_main_bot_processes


TRAVEL_MARKERS = (
    "travel-ai/run_server.py",
    "/travel-ai/",
    " -m travel_ai",
    "bin/travel-ai",
)


def find_travel_processes() -> list[ProcessInfo]:
    out: list[ProcessInfo] = []
    for pid, cmd in _ps_rows():
        if any(m in cmd for m in TRAVEL_MARKERS) and "grep" not in cmd:
            out.append(ProcessInfo(pid=pid, command=cmd))
    return out


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pidfile(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip().split()[0]
        return int(raw)
    except Exception:
        return None


def pidfile_matches_live_process(path: Path, *, markers: tuple[str, ...]) -> dict[str, Any]:
    """PASS only if pidfile PID is alive AND command matches markers."""
    pid = read_pidfile(path)
    if pid is None:
        return {"ok": False, "reason": "missing_or_invalid_pidfile", "pid": None}
    if not pid_alive(pid):
        return {"ok": False, "reason": "stale_pidfile", "pid": pid}
    # confirm command line
    for p, cmd in _ps_rows():
        if p == pid:
            if any(m in cmd for m in markers):
                return {"ok": True, "reason": "live", "pid": pid, "command": cmd[:200]}
            return {"ok": False, "reason": "pid_alive_wrong_command", "pid": pid, "command": cmd[:200]}
    return {"ok": False, "reason": "pid_not_in_ps", "pid": pid}


def trading_paper_status() -> dict[str, Any]:
    from bot.config import TRADING_MODE, is_live_trading_enabled, is_paper_mode

    procs = find_main_bot_processes()
    return {
        "running": len(procs) > 0,
        "count": len(procs),
        "pids": [p.pid for p in procs],
        "trading_mode": TRADING_MODE,
        "is_paper": is_paper_mode(),
        "is_live": is_live_trading_enabled(),
        "duplicates": len(procs) > 1,
        "procs": [{"pid": p.pid, "command": p.command[:160]} for p in procs],
    }


def travel_status(*, root: Path | None = None) -> dict[str, Any]:
    root = root or Path(os.environ.get("TRAVEL_AI_ROOT", str(Path.home() / "travel-ai"))).expanduser()
    pidfile = root / "run" / "travel-ai.pid"
    procs = find_travel_processes()
    pf = pidfile_matches_live_process(pidfile, markers=TRAVEL_MARKERS)
    return {
        "root": str(root),
        "running": len(procs) > 0,
        "count": len(procs),
        "pids": [p.pid for p in procs],
        "pidfile": str(pidfile),
        "pidfile_ok": bool(pf.get("ok")),
        "pidfile_detail": pf,
        "duplicates": len(procs) > 1,
        "procs": [{"pid": p.pid, "command": p.command[:160]} for p in procs],
    }
