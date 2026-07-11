"""Phase E.5 alert engine configuration."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_HEARTBEAT_SEC = int(os.getenv("ME_TELEGRAM_HEARTBEAT_SEC", "1800"))
DAILY_DIGEST_HOUR_UTC = int(os.getenv("ME_DAILY_DIGEST_HOUR_UTC", "21"))
WEEKLY_DIGEST_WEEKDAY = int(os.getenv("ME_WEEKLY_DIGEST_WEEKDAY", "0"))  # Monday
DASHBOARD_API_HOST = os.getenv("ME_DASHBOARD_API_HOST", "127.0.0.1")
DASHBOARD_API_PORT = int(os.getenv("ME_DASHBOARD_API_PORT", "8765"))


def telegram_heartbeat_enabled() -> bool:
    return os.getenv("ME_TELEGRAM_HEARTBEAT_ENABLED", "true").lower() in ("1", "true", "yes")


def daily_digest_enabled() -> bool:
    return os.getenv("ME_DAILY_DIGEST_ENABLED", "true").lower() in ("1", "true", "yes")


def weekly_digest_enabled() -> bool:
    return os.getenv("ME_WEEKLY_DIGEST_ENABLED", "true").lower() in ("1", "true", "yes")
