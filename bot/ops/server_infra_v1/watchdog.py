"""Watchdog — restart dead KeepAlive AI-server services every 5 minutes."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from bot.ops.server_infra_v1.config import AI_SERVER_SERVICES, LAUNCH_AGENTS, REPO
from bot.ops.server_infra_v1.launchd_mgr import launchctl_loaded_labels


def _kickstart(label: str) -> tuple[bool, str]:
    uid = os.getuid()
    r = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    ok = r.returncode == 0
    return ok, (r.stderr or r.stdout or "").strip()


def run_watchdog(*, dry_run: bool = False) -> dict[str, Any]:
    t0 = time.time()
    loaded = launchctl_loaded_labels()
    actions: list[dict[str, Any]] = []
    for svc in AI_SERVER_SERVICES:
        if svc["schedule"] != "keepalive":
            continue
        label = svc["label"]
        installed = (LAUNCH_AGENTS / f"{label}.plist").exists()
        if not installed:
            actions.append({"label": label, "action": "skip", "reason": "not_installed"})
            continue
        if label in loaded:
            actions.append({"label": label, "action": "ok", "reason": "loaded"})
            continue
        if dry_run:
            actions.append({"label": label, "action": "would_restart", "reason": "not_loaded"})
            continue
        ok, detail = _kickstart(label)
        # if not loaded, try bootstrap first
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
            "detail": detail[:200],
        })

    log_dir = REPO / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    line = f"{int(time.time())} watchdog actions={actions}\n"
    (log_dir / "ai-server-watchdog.log").open("a", encoding="utf-8").write(line)

    restarted = sum(1 for a in actions if a.get("action") == "restarted")
    failed = sum(1 for a in actions if a.get("action") == "restart_failed")
    terminal = "\n".join([
        "AI SERVER WATCHDOG V1",
        "",
        f"elapsed={round(time.time()-t0,3)}s dry_run={dry_run}",
        f"restarted={restarted} failed={failed}",
        *[f"- {a['label']}: {a['action']}" for a in actions],
        "",
    ])
    return {
        "ok": failed == 0,
        "actions": actions,
        "restarted": restarted,
        "failed": failed,
        "terminal": terminal,
    }
