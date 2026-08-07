"""launchd autostart audit + templates for Hermes Autonomous V2."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPO = BASE_DIR
DEPLOY = REPO / "deploy" / "macos"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

# Required autostart set (user-specified labels)
AUTOSTART_SERVICES: tuple[dict[str, str], ...] = (
    {
        "key": "hermes",
        "label": "com.polymarket.hermes-daily",
        "cmd": "daily-hermes-report",
        "schedule": "daily",
    },
    {
        "key": "learning",
        "label": "com.polymarket.learning",
        "cmd": "learning-worker",
        "schedule": "keepalive",
    },
    {
        "key": "observe",
        "label": "com.polymarket.observe",
        "cmd": "observe-run --universe tradfi-observe",
        "schedule": "keepalive",
    },
    {
        "key": "event-engine",
        "label": "com.polymarket.event-engine",
        "cmd": "event-engine-run",
        "schedule": "keepalive",
    },
    {
        "key": "dashboard",
        "label": "com.polymarket.dashboard",
        "cmd": "dashboard-api-serve",
        "schedule": "keepalive",
    },
    {
        "key": "news-intel",
        "label": "com.polymarket.news-intel",
        "cmd": "news-intel-worker",
        "schedule": "keepalive",
    },
    {
        "key": "ai-worker",
        "label": "com.polymarket.ai-worker",
        "cmd": "ai-worker-run",
        "schedule": "keepalive",
    },
    {
        "key": "multi-source",
        "label": "com.polymarket.multi-source",
        "cmd": "multi-source-run",
        "schedule": "keepalive",
    },
    {
        "key": "narrative",
        "label": "com.polymarket.narrative",
        "cmd": "narrative-engine-run",
        "schedule": "keepalive",
    },
    {
        "key": "g3",
        "label": "com.polymarket.g3",
        "cmd": "g3-run",
        "schedule": "keepalive",
    },
)


def _plist_body(label: str, wrapper: str, *, daily: bool) -> str:
    schedule = ""
    if daily:
        schedule = """
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>15</integer>
    </dict>
"""
    else:
        schedule = """
    <key>KeepAlive</key>
    <true/>
"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{wrapper}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{REPO}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
{schedule}
    <key>StandardOutPath</key>
    <string>{REPO}/logs/launchd-{label}.log</string>
    <key>StandardErrorPath</key>
    <string>{REPO}/logs/launchd-{label}.err.log</string>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
"""


def _wrapper_body(cmd: str) -> str:
    return f"""#!/usr/bin/env bash
# launchd-safe wrapper — loads project .env then runs market_events command
set -euo pipefail
REPO="{REPO}"
cd "${{REPO}}"
if [[ -f "${{REPO}}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${{REPO}}/.env"
  set +a
fi
PY="${{REPO}}/.venv/bin/python"
if [[ ! -x "${{PY}}" ]]; then
  PY="$(command -v python3)"
fi
exec "${{PY}}" -m bot.research.market_events {cmd}
"""


def write_autostart_templates() -> dict[str, Any]:
    DEPLOY.mkdir(parents=True, exist_ok=True)
    (REPO / "logs").mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for svc in AUTOSTART_SERVICES:
        key = svc["key"]
        wrapper = DEPLOY / f"run-{key}.sh"
        plist = DEPLOY / f"{svc['label']}.plist"
        wrapper.write_text(_wrapper_body(svc["cmd"]), encoding="utf-8")
        wrapper.chmod(0o755)
        plist.write_text(
            _plist_body(svc["label"], str(wrapper), daily=(svc["schedule"] == "daily")),
            encoding="utf-8",
        )
        written.append(svc["label"])
    return {"ok": True, "templates": written, "deploy_dir": str(DEPLOY)}


def _launchctl_loaded_labels() -> set[str]:
    labels: set[str] = set()
    try:
        uid = os.getuid()
        out = subprocess.check_output(
            ["launchctl", "print", f"gui/{uid}"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("com.polymarket."):
                labels.add(line.split()[0])
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["launchctl", "list"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        for line in out.splitlines():
            for svc in AUTOSTART_SERVICES:
                if svc["label"] in line:
                    labels.add(svc["label"])
    except Exception:
        pass
    return labels


def audit_autostart() -> dict[str, Any]:
    """Check templates exist + whether LaunchAgents are installed/loaded."""
    templates = write_autostart_templates()
    loaded = _launchctl_loaded_labels()
    rows: list[dict[str, Any]] = []
    missing_install: list[str] = []
    missing_loaded: list[str] = []
    for svc in AUTOSTART_SERVICES:
        label = svc["label"]
        agent = LAUNCH_AGENTS / f"{label}.plist"
        template = DEPLOY / f"{label}.plist"
        installed = agent.exists()
        is_loaded = label in loaded
        if not installed:
            missing_install.append(label)
        if not is_loaded:
            missing_loaded.append(label)
        rows.append({
            "key": svc["key"],
            "label": label,
            "template": template.exists(),
            "installed_launch_agent": installed,
            "loaded": is_loaded,
            "schedule": svc["schedule"],
        })
    ok = not missing_install and not missing_loaded
    lines = [
        "HERMES AUTOSTART AUDIT V2",
        "",
        f"templates_dir={DEPLOY}",
        f"launch_agents={LAUNCH_AGENTS}",
        f"ok={ok}",
        "",
    ]
    for r in rows:
        lines.append(
            f"- {r['key']}: template={r['template']} installed={r['installed_launch_agent']} "
            f"loaded={r['loaded']} schedule={r['schedule']}"
        )
    if missing_install:
        lines.append("")
        lines.append("MISSING LaunchAgents (copy from deploy/macos then bootstrap):")
        for lab in missing_install:
            lines.append(f"  cp {DEPLOY}/{lab}.plist {LAUNCH_AGENTS}/")
            lines.append(f"  launchctl bootstrap gui/$(id -u) {LAUNCH_AGENTS}/{lab}.plist")
            lines.append(f"  launchctl enable gui/$(id -u)/{lab}")
    if missing_loaded and not missing_install:
        lines.append("")
        lines.append("Installed but not loaded — kickstart/bootstrap required after reboot.")
    lines.append("")
    terminal = "\n".join(lines)
    return {
        "ok": ok,
        "research_only": True,
        "rows": rows,
        "missing_install": missing_install,
        "missing_loaded": missing_loaded,
        "templates": templates,
        "terminal": terminal,
    }


__all__ = [
    "AUTOSTART_SERVICES",
    "audit_autostart",
    "write_autostart_templates",
]
