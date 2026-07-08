"""Stage 3 production source DB requirements."""

from __future__ import annotations

import os

from bot.research.futures.db_config import REASON_POSTGRES_URL_MISSING, REASON_SQLITE_SOURCE_EMPTY
from bot.research.futures.source_reader import (
    SourceConfigError,
    SourceReader,
    open_configured_source_reader,
)


class Stage3SourceRequiredError(RuntimeError):
    """Stage 3 command cannot run without production PostgreSQL source."""


def render_stage3_source_db_required() -> str:
    url_val = os.getenv("FUTURES_SOURCE_DATABASE_URL")
    display = "missing" if not url_val or not url_val.strip() else url_val.strip()
    return (
        "------------------------------------\n"
        "Stage 3 requires production source DB.\n"
        "\n"
        "Either:\n"
        "\n"
        "1) run on Mac Mini\n"
        "\n"
        "or\n"
        "\n"
        "2) export FUTURES_SOURCE_DATABASE_URL\n"
        "\n"
        "Current environment:\n"
        f"FUTURES_SOURCE_DATABASE_URL = {display}\n"
        "------------------------------------"
    )


def open_stage3_source_reader() -> SourceReader:
    """Open production source reader or raise a user-friendly Stage 3 error."""
    try:
        return open_configured_source_reader()
    except SourceConfigError as exc:
        if exc.reason_code in (REASON_POSTGRES_URL_MISSING, REASON_SQLITE_SOURCE_EMPTY):
            raise Stage3SourceRequiredError(render_stage3_source_db_required()) from None
        raise
