"""Futures Intelligence Agent configuration."""

from __future__ import annotations

import os

PARSER_VERSION = "deterministic_v2"
AGENT_SCHEMA_VERSION = 1

# Tables the agent write adapter may touch
AGENT_TABLE_ALLOWLIST = frozenset({
    "futures_agent_migrations",
    "futures_agent_inputs",
    "futures_agent_signals",
    "futures_agent_targets",
})

INPUT_TYPE_FORWARDED = "forwarded"
INPUT_TYPE_CLI = "cli"
INPUT_TYPE_CHANNEL = "channel"

STATUS_RECEIVED = "received"
STATUS_PARSED = "parsed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_REJECTED = "rejected_non_signal"
STATUS_PENDING_SNAPSHOT = "pending_snapshot"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

PARSE_STATUS_SUCCESS = "SUCCESS"
PARSE_STATUS_PARTIAL = "PARTIAL"
PARSE_STATUS_FAILED = "FAILED"
PARSE_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"


def get_agent_database_url() -> str | None:
    for key in (
        "FUTURES_AGENT_DATABASE_URL",
        "FUTURES_SOURCE_DATABASE_URL",
        "TELEGRAM_DATABASE_URL",
    ):
        val = os.getenv(key)
        if val:
            return val
    return None


def get_agent_sqlite_fallback_path() -> str | None:
    return os.getenv("FUTURES_AGENT_SQLITE_PATH")


def telegram_notify_enabled() -> bool:
    return bool(
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_AGENT_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    )


def get_telegram_chat_id() -> str | None:
    return os.getenv("TELEGRAM_AGENT_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
