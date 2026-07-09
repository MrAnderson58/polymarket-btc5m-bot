"""Observation universe scope selection."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.instrument_master import (
    load_observe_instruments,
    resolve_instruments_by_symbols,
)


def select_observe_universe(
    conn: Any,
    *,
    mode: str = "tradfi-observe",
    explicit_symbols: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if explicit_symbols:
        rows = resolve_instruments_by_symbols(conn, explicit_symbols)
        symbols = [r["canonical_asset"] for r in rows]
        reason = f"explicit_symbols={explicit_symbols} resolved={len(rows)}"
        version_tag = f"observe-explicit-{int(time.time())}"
        _log(conn, version_tag, symbols, reason)
        return [dict(r) for r in rows], version_tag

    if mode == "tradfi-observe":
        rows = load_observe_instruments(conn, scope="tradfi")
        symbols = [r["canonical_asset"] for r in rows]
        reason = f"mode=tradfi-observe bybit_tradfi={len(symbols)} (excludes crypto)"
        version_tag = f"tradfi-observe-{int(time.time())}"
        _log(conn, version_tag, symbols, reason)
        return [dict(r) for r in rows], version_tag

    if mode == "all-observe":
        rows = load_observe_instruments(conn, scope="all")
        symbols = [r["canonical_asset"] for r in rows]
        reason = f"mode=all-observe total={len(symbols)}"
        version_tag = f"all-observe-{int(time.time())}"
        _log(conn, version_tag, symbols, reason)
        return [dict(r) for r in rows], version_tag

    if mode == "crypto-observe":
        rows = load_observe_instruments(conn, scope="crypto")
        symbols = [r["canonical_asset"] for r in rows]
        reason = f"mode=crypto-observe crypto={len(symbols)}"
        version_tag = f"crypto-observe-{int(time.time())}"
        _log(conn, version_tag, symbols, reason)
        return [dict(r) for r in rows], version_tag

    rows = load_observe_instruments(conn, scope="tradfi")
    symbols = [r["canonical_asset"] for r in rows]
    reason = f"mode={mode} unknown_fallback_tradfi-observe"
    version_tag = f"observe-{int(time.time())}"
    _log(conn, version_tag, symbols, reason)
    return [dict(r) for r in rows], version_tag


def _log(conn: Any, version_tag: str, symbols: list[str], reason: str) -> None:
    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_universe_log (version_tag, symbols_json, selection_reason, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (version_tag, json.dumps(symbols), reason, int(time.time())),
    )
