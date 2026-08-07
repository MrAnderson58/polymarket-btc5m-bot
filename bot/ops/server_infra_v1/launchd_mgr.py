"""launchd template writers + install/audit for Server Infra V1."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from bot.ops.server_infra_v1.config import (
    AI_SERVER_SERVICES,
    DEPLOY,
    INFRA_AGENTS,
    LAUNCH_AGENTS,
    REPO,
    TRAVEL_AI_ROOT,
)


def _plist_header(label: str, wrapper: Path) -> str:
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
"""


def _plist_footer(label: str) -> str:
    return f"""    <key>StandardOutPath</key>
    <string>{REPO}/logs/launchd-{label}.log</string>
    <key>StandardErrorPath</key>
    <string>{REPO}/logs/launchd-{label}.err.log</string>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
"""


def render_plist(
    label: str,
    wrapper: Path,
    *,
    schedule: str,
    hour: int = 6,
    minute: int = 15,
    interval_sec: int = 300,
) -> str:
    body = _plist_header(label, wrapper)
    if schedule == "keepalive":
        body += "    <key>KeepAlive</key>\n    <true/>\n"
    elif schedule == "daily":
        body += f"""    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>
"""
    elif schedule == "interval":
        body += f"""    <key>StartInterval</key>
    <integer>{interval_sec}</integer>
    <key>KeepAlive</key>
    <false/>
"""
    elif schedule == "run_at_load":
        # boot warmup: RunAtLoad only + short delay inside script
        body += "    <key>KeepAlive</key>\n    <false/>\n"
    body += _plist_footer(label)
    return body


def write_generic_wrapper(name: str, module_cmd: str) -> Path:
    DEPLOY.mkdir(parents=True, exist_ok=True)
    path = DEPLOY / name
    path.write_text(
        f"""#!/usr/bin/env bash
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
exec "${{PY}}" -m {module_cmd}
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def write_travel_wrapper() -> Path:
    DEPLOY.mkdir(parents=True, exist_ok=True)
    path = DEPLOY / "run-travel-ai.sh"
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
ROOT="${{TRAVEL_AI_ROOT:-{TRAVEL_AI_ROOT}}}"
if [[ ! -d "${{ROOT}}" ]]; then
  echo "Travel AI root missing: ${{ROOT}} (set TRAVEL_AI_ROOT)" >&2
  # stay alive-ish for launchd visibility without spinning CPU
  sleep 3600
  exit 0
fi
cd "${{ROOT}}"
if [[ -f "${{ROOT}}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${{ROOT}}/.env"
  set +a
fi
if [[ -x "${{ROOT}}/.venv/bin/python" ]]; then
  PY="${{ROOT}}/.venv/bin/python"
else
  PY="$(command -v python3)"
fi
if [[ -f "${{ROOT}}/run_server.py" ]]; then
  exec "${{PY}}" "${{ROOT}}/run_server.py"
fi
if [[ -f "${{ROOT}}/-m" ]]; then
  true
fi
# common entrypoints
if "${{PY}}" -c "import travel_ai" 2>/dev/null; then
  exec "${{PY}}" -m travel_ai
fi
echo "Travel AI entrypoint not found under ${{ROOT}}" >&2
sleep 3600
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def write_infra_wrappers() -> list[str]:
    written = []
    mapping = {
        "run-ai-server-watchdog.sh": "bot.ops.server_infra_v1 watchdog",
        "run-ai-server-backup.sh": "bot.ops.server_infra_v1 backup",
        "run-ai-server-git-morning.sh": "bot.ops.server_infra_v1 git-morning",
        "run-ai-server-boot-warmup.sh": "bot.ops.server_infra_v1 boot-warmup",
    }
    for name, cmd in mapping.items():
        # bot.ops.server_infra_v1 is a package __main__
        parts = cmd.split()
        module = parts[0]
        args = " ".join(parts[1:])
        DEPLOY.mkdir(parents=True, exist_ok=True)
        path = DEPLOY / name
        path.write_text(
            f"""#!/usr/bin/env bash
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
exec "${{PY}}" -m {module} {args}
""",
            encoding="utf-8",
        )
        path.chmod(0o755)
        written.append(name)
    write_travel_wrapper()
    written.append("run-travel-ai.sh")
    return written


def write_all_templates() -> dict[str, Any]:
    (REPO / "logs").mkdir(parents=True, exist_ok=True)
    DEPLOY.mkdir(parents=True, exist_ok=True)
    write_infra_wrappers()
    labels: list[str] = []
    for svc in AI_SERVER_SERVICES:
        wrapper = DEPLOY / svc["wrapper"]
        if not wrapper.exists() and svc["key"] != "travel":
            # ensure missing wrappers for known market_events cmds exist via hermes set
            pass
        if svc["key"] == "travel":
            write_travel_wrapper()
        plist = DEPLOY / f"{svc['label']}.plist"
        hour, minute = 6, 30
        if svc["schedule"] == "daily" and svc["key"] == "hermes":
            hour, minute = 6, 15
        plist.write_text(
            render_plist(
                svc["label"],
                DEPLOY / svc["wrapper"],
                schedule=svc["schedule"],
                hour=hour,
                minute=minute,
            ),
            encoding="utf-8",
        )
        labels.append(svc["label"])

    for agent in INFRA_AGENTS:
        wrapper = DEPLOY / agent["wrapper"]
        plist = DEPLOY / f"{agent['label']}.plist"
        sched = agent["schedule"]
        plist.write_text(
            render_plist(
                agent["label"],
                wrapper,
                schedule=sched,
                hour=int(agent.get("hour") or 3),
                minute=int(agent.get("minute") or 15),
                interval_sec=int(agent.get("interval_sec") or 300),
            ),
            encoding="utf-8",
        )
        labels.append(agent["label"])
    return {"ok": True, "labels": labels, "deploy": str(DEPLOY)}


def launchctl_loaded_labels() -> set[str]:
    labels: set[str] = set()
    try:
        out = subprocess.check_output(
            ["launchctl", "list"], text=True, stderr=subprocess.DEVNULL, timeout=20
        )
        for line in out.splitlines():
            for svc in list(AI_SERVER_SERVICES) + list(INFRA_AGENTS):
                if svc["label"] in line:
                    labels.add(svc["label"])
    except Exception:
        pass
    return labels


def install_launch_agents(*, bootstrap: bool = True) -> dict[str, Any]:
    """Copy templates into ~/Library/LaunchAgents and optionally bootstrap."""
    write_all_templates()
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    errors: list[str] = []
    uid = os.getuid()
    for svc in list(AI_SERVER_SERVICES) + list(INFRA_AGENTS):
        label = svc["label"]
        src = DEPLOY / f"{label}.plist"
        if not src.exists():
            errors.append(f"missing template {src}")
            continue
        dst = LAUNCH_AGENTS / f"{label}.plist"
        shutil.copy2(src, dst)
        installed.append(label)
        if bootstrap:
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/{label}"],
                check=False,
                capture_output=True,
            )
            r = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(dst)],
                check=False,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0 and "already bootstrapped" not in (r.stderr or "").lower():
                errors.append(f"{label}: {r.stderr.strip() or r.stdout.strip()}")
            subprocess.run(
                ["launchctl", "enable", f"gui/{uid}/{label}"],
                check=False,
                capture_output=True,
            )
    loaded = launchctl_loaded_labels()
    return {
        "ok": not errors,
        "installed": installed,
        "loaded": sorted(loaded),
        "errors": errors,
    }


def audit_launchd() -> dict[str, Any]:
    write_all_templates()
    loaded = launchctl_loaded_labels()
    rows = []
    missing = []
    for svc in list(AI_SERVER_SERVICES) + list(INFRA_AGENTS):
        label = svc["label"]
        installed = (LAUNCH_AGENTS / f"{label}.plist").exists()
        is_loaded = label in loaded
        if not installed or not is_loaded:
            missing.append(label)
        rows.append({
            "key": svc["key"],
            "label": label,
            "installed": installed,
            "loaded": is_loaded,
            "template": (DEPLOY / f"{label}.plist").exists(),
        })
    return {
        "ok": not missing,
        "rows": rows,
        "missing": missing,
        "loaded": sorted(loaded),
    }
