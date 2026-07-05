"""Futures research DB configuration — separate source (read-only) and research storage."""

from __future__ import annotations

import os

from bot.config import DATABASE_PATH

REASON_POSTGRES_URL_MISSING = "POSTGRES_URL_MISSING"
REASON_POSTGRES_CONNECT_FAILED = "POSTGRES_CONNECT_FAILED"
REASON_POSTGRES_DRIVER_MISSING = "POSTGRES_DRIVER_MISSING"
REASON_SQLITE_SOURCE_EMPTY = "SQLITE_SOURCE_EMPTY"
REASON_SOURCE_BACKEND_MISMATCH = "SOURCE_BACKEND_MISMATCH"


def get_futures_source_database_url() -> str | None:
    for key in ("FUTURES_SOURCE_DATABASE_URL", "TELEGRAM_DATABASE_URL", "DATABASE_URL"):
        val = os.getenv(key)
        if val and val.startswith(("postgres://", "postgresql://")):
            return val
    explicit = os.getenv("FUTURES_SOURCE_DATABASE_URL") or os.getenv("TELEGRAM_DATABASE_URL")
    return explicit or None


def get_futures_source_backend() -> str:
    return os.getenv("FUTURES_SOURCE_BACKEND", "auto").strip().lower()


def get_futures_require_postgres() -> bool:
    return os.getenv("FUTURES_REQUIRE_POSTGRES", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def get_futures_source_connect_timeout_sec() -> float:
    return float(os.getenv("FUTURES_SOURCE_CONNECT_TIMEOUT_SEC", "5"))


def get_futures_source_statement_timeout_ms() -> int:
    return int(os.getenv("FUTURES_SOURCE_STATEMENT_TIMEOUT_MS", "30000"))


def get_futures_research_database_path() -> str:
    return os.getenv("FUTURES_RESEARCH_DATABASE_PATH") or str(DATABASE_PATH)
