"""Server Infrastructure V1 — Mac mini autonomous AI-server config."""

from __future__ import annotations

import os
from pathlib import Path

from bot.research.market_events.config import BASE_DIR

REPO = BASE_DIR
DEPLOY = REPO / "deploy" / "macos"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
BACKUP_ROOT = REPO / "backups" / "ai_server_v1"
BACKUP_RETENTION_DAYS = 30
WATCHDOG_INTERVAL_SEC = 300

# Optional Travel AI root (override via env).
TRAVEL_AI_ROOT = Path(
    os.environ.get(
        "TRAVEL_AI_ROOT",
        str(Path.home() / "travel-ai"),
    )
).expanduser()

# Core AI-lab services that must survive reboot (KeepAlive / scheduled).
AI_SERVER_SERVICES: tuple[dict[str, str], ...] = (
    {
        "key": "trading",
        "label": "com.polymarket.bot-main",
        "title": "Trading",
        "wrapper": "run-bot-main.sh",
        "schedule": "keepalive",
    },
    {
        "key": "travel",
        "label": "com.polymarket.travel-ai",
        "title": "Travel",
        "wrapper": "run-travel-ai.sh",
        "schedule": "keepalive",
    },
    {
        "key": "hermes",
        "label": "com.polymarket.hermes-daily",
        "title": "Hermes",
        "wrapper": "run-hermes.sh",
        "schedule": "daily",
    },
    {
        "key": "dashboard",
        "label": "com.polymarket.dashboard",
        "title": "Dashboard",
        "wrapper": "run-dashboard.sh",
        "schedule": "keepalive",
    },
    {
        "key": "learning",
        "label": "com.polymarket.learning",
        "title": "Learning",
        "wrapper": "run-learning.sh",
        "schedule": "keepalive",
    },
    {
        "key": "event-engine",
        "label": "com.polymarket.event-engine",
        "title": "Event Engine",
        "wrapper": "run-event-engine.sh",
        "schedule": "keepalive",
    },
    {
        "key": "news",
        "label": "com.polymarket.news-intel",
        "title": "News",
        "wrapper": "run-news-intel.sh",
        "schedule": "keepalive",
    },
    {
        "key": "multi-source",
        "label": "com.polymarket.multi-source",
        "title": "Multi-source",
        "wrapper": "run-multi-source.sh",
        "schedule": "keepalive",
    },
    {
        "key": "ai-worker",
        "label": "com.polymarket.ai-worker",
        "title": "AI Worker",
        "wrapper": "run-ai-worker.sh",
        "schedule": "keepalive",
    },
    {
        "key": "observe",
        "label": "com.polymarket.observe",
        "title": "Observe",
        "wrapper": "run-observe.sh",
        "schedule": "keepalive",
    },
)

INFRA_AGENTS: tuple[dict[str, str], ...] = (
    {
        "key": "watchdog",
        "label": "com.polymarket.ai-server-watchdog",
        "wrapper": "run-ai-server-watchdog.sh",
        "schedule": "interval",
        "interval_sec": str(WATCHDOG_INTERVAL_SEC),
    },
    {
        "key": "backup",
        "label": "com.polymarket.ai-server-backup",
        "wrapper": "run-ai-server-backup.sh",
        "schedule": "daily",
        "hour": "3",
        "minute": "15",
    },
    {
        "key": "git-morning",
        "label": "com.polymarket.ai-server-git-morning",
        "wrapper": "run-ai-server-git-morning.sh",
        "schedule": "daily",
        "hour": "7",
        "minute": "0",
    },
    {
        "key": "boot-warmup",
        "label": "com.polymarket.ai-server-boot-warmup",
        "wrapper": "run-ai-server-boot-warmup.sh",
        "schedule": "run_at_load",
    },
)

DISK_WARN_PCT = 90.0
RAM_WARN_PCT = 95.0
CPU_WARN_LOAD = 16.0
