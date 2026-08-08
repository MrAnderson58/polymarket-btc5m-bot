"""Watchdog — restart dead KeepAlive services only when real process is missing."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

from bot.ops.server_infra_v1.config import AI_SERVER_SERVICES, LAUNCH_AGENTS, REPO
from bot.ops.server_infra_v1.launchd_mgr import launchctl_loaded_labels
from bot.ops.server_infra_v1.process_health import (
    find_travel_processes,
    trading_paper_status,
)


def _kickstart(label: str) -> tuple[bool, str]:
    uid = os.getuid()
    r = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0, (r.stderr or r.stdout or "").strip()


def _needs_restart(svc: dict[str, str], loaded: set[str]) -> tuple[bool, str]:
    label = svc["label"]
    installed = (LAUNCH_AGENTS / f"{label}.plist").exists()
    if not installed:
        return False, "not_installed"
    if svc["key"] == "trading":
        st = trading_paper_status()
        if st["running"] and not st["duplicates"]:
            return False, "healthy_process"
        return True, "missing_or_duplicate_bot_main"
    if svc["key"] == "travel":
        procs = find_travel_processes()
        if len(procs) == 1:
            return False, "healthy_process"
        if len(procs) > 1:
            return True, "duplicate_travel"
        return True, "missing_travel_process"
    if svc["schedule"] != "keepalive":
        return False, "scheduled_skip"
    if label in loaded:
        return False, "loaded"
    return True, "not_loaded"


def run_watchdog(*, dry_run: bool = False) -> dict[str, Any]:
    t0 = time.time()
    loaded = launchctl_loaded_labels()
    actions: list[dict[str, Any]] = []
    for svc in AI_SERVER_SERVICES:
        label = svc["label"]
        need, reason = _needs_restart(svc, loaded)
        if not need:
            actions.append({"label": label, "action": "ok", "reason": reason})
            continue
        if dry_run:
            actions.append({"label": label, "action": "would_restart", "reason": reason})
            continue
        ok, detail = _kickstart(label)
        if not ok:
            plist = LAUNCH_AGENTS / f"{label}.plist"
            if plist.exists():
                subprocess.run(
                    ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                    check=False,
                    capture_output=True,
                )
                ok, detail = _kickstart(label)
        actions.append({
            "label": label,
            "action": "restarted" if ok else "restart_failed",
            "reason": reason,
            "detail": detail[:200],
        })

    log_dir = REPO / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "ai-server-watchdog.log").open("a", encoding="utf-8").write(
        f"{int(time.time())} watchdog actions={actions}\n"
    )

    restarted = sum(1 for a in actions if a.get("action") == "restarted")
    failed = sum(1 for a in actions if a.get("action") == "restart_failed")
    terminal = "\n".join([
        "AI SERVER WATCHDOG V1",
        "",
        f"elapsed={round(time.time() - t0, 3)}s dry_run={dry_run}",
        f"restarted={restarted} failed={failed}",
        *[f"- {a['label']}: {a['action']} ({a.get('reason')})" for a in actions],
        "",
    ])
    return {
        "ok": failed == 0,
        "actions": actions,
        "restarted": restarted,
        "failed": failed,
        "terminal": terminal,
    }
