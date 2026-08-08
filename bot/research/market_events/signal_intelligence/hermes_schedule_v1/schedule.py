"""Hermes schedule helpers — single daily + single weekly (no duplicates)."""

from __future__ import annotations

from bot.ops.server_infra_v1.config import DEPLOY, LAUNCH_AGENTS, REPO
from bot.ops.server_infra_v1.launchd_mgr import render_plist

HERMES_DAILY_LABEL = "com.polymarket.hermes-daily"
HERMES_WEEKLY_LABEL = "com.polymarket.hermes-weekly"


def ensure_hermes_schedule_templates() -> dict:
    """Write/refresh daily+weekly wrappers and plists (idempotent)."""
    DEPLOY.mkdir(parents=True, exist_ok=True)
    # daily wrapper already exists as run-hermes.sh — point to hermes-daily command
    daily_wrap = DEPLOY / "run-hermes.sh"
    daily_wrap.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
REPO="{REPO}"
cd "${{REPO}}"
if [[ -f "${{REPO}}/.env" ]]; then set -a; source "${{REPO}}/.env"; set +a; fi
PY="${{REPO}}/.venv/bin/python"
[[ -x "${{PY}}" ]] || PY="$(command -v python3)"
# Stage: package then DeepSeek daily (research-only)
"${{PY}}" -m bot.research.market_events daily-research-package
exec "${{PY}}" -m bot.research.market_events hermes-daily
""",
        encoding="utf-8",
    )
    daily_wrap.chmod(0o755)

    weekly_wrap = DEPLOY / "run-hermes-weekly.sh"
    weekly_wrap.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
REPO="{REPO}"
cd "${{REPO}}"
if [[ -f "${{REPO}}/.env" ]]; then set -a; source "${{REPO}}/.env"; set +a; fi
PY="${{REPO}}/.venv/bin/python"
[[ -x "${{PY}}" ]] || PY="$(command -v python3)"
exec "${{PY}}" -m bot.research.market_events hermes-weekly
""",
        encoding="utf-8",
    )
    weekly_wrap.chmod(0o755)

    (DEPLOY / f"{HERMES_DAILY_LABEL}.plist").write_text(
        render_plist(HERMES_DAILY_LABEL, daily_wrap, schedule="daily", hour=6, minute=15),
        encoding="utf-8",
    )
    weekly_plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{HERMES_WEEKLY_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{weekly_wrap}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{REPO}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{REPO}/logs/launchd-{HERMES_WEEKLY_LABEL}.log</string>
    <key>StandardErrorPath</key>
    <string>{REPO}/logs/launchd-{HERMES_WEEKLY_LABEL}.err.log</string>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
"""
    (DEPLOY / f"{HERMES_WEEKLY_LABEL}.plist").write_text(weekly_plist, encoding="utf-8")
    return {
        "ok": True,
        "daily_label": HERMES_DAILY_LABEL,
        "weekly_label": HERMES_WEEKLY_LABEL,
        "daily_installed": (LAUNCH_AGENTS / f"{HERMES_DAILY_LABEL}.plist").exists(),
        "weekly_template": str(DEPLOY / f"{HERMES_WEEKLY_LABEL}.plist"),
    }
