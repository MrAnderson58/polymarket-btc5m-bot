"""Monitored universe selection — versioned logging."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.config import CORE_SYMBOLS
from bot.research.market_events.db import insert_returning_id


def select_universe(
    conn: Any,
    *,
    mode: str = "core",
    extra_symbols: list[str] | None = None,
    max_symbols: int = 15,
) -> tuple[list[str], str]:
    symbols = list(CORE_SYMBOLS)
    reason = f"mode={mode} core={len(CORE_SYMBOLS)}"
    if extra_symbols:
        for s in extra_symbols:
            if s.upper() not in symbols and len(symbols) < max_symbols:
                symbols.append(s.upper())
        reason += f" extra={len(extra_symbols)}"
    symbols = symbols[:max_symbols]
    version_tag = f"{mode}-{int(time.time())}"
    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_universe_log (version_tag, symbols_json, selection_reason, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (version_tag, json.dumps(symbols), reason, int(time.time())),
    )
    return symbols, version_tag
