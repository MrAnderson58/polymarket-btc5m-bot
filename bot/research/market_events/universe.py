"""Monitored universe selection — versioned logging."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.config import CORE_SYMBOLS
from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.instrument_master import (
    load_active_instruments,
    load_paper_instruments,
    resolve_instruments_by_symbols,
)


def select_universe(
    conn: Any,
    *,
    mode: str = "core",
    explicit_symbols: list[str] | None = None,
    max_symbols: int = 15,
) -> tuple[list[str], str]:
    if explicit_symbols:
        rows = resolve_instruments_by_symbols(conn, explicit_symbols)
        symbols = [r["canonical_asset"] for r in rows][:max_symbols]
        reason = f"explicit_symbols={explicit_symbols} resolved={len(symbols)}"
        if not symbols:
            reason += " (no registry match — pass canonical or venue_symbol after discover)"
        version_tag = f"explicit-{int(time.time())}"
        _log_universe(conn, version_tag, symbols, reason)
        return symbols, version_tag

    if mode == "core":
        symbols = list(CORE_SYMBOLS)[:max_symbols]
        reason = f"mode=core e1_binance crypto={len(symbols)}"
        version_tag = f"core-{int(time.time())}"
        _log_universe(conn, version_tag, symbols, reason)
        return symbols, version_tag

    if mode == "tradfi-liquid":
        rows = load_paper_instruments(conn, include_crypto=False, tradfi_only=True)
        symbols = [r["canonical_asset"] for r in rows][:max_symbols]
        reason = f"mode=tradfi-liquid paper_enabled={len(symbols)}"
        version_tag = f"tradfi-liquid-{int(time.time())}"
        _log_universe(conn, version_tag, symbols, reason)
        return symbols, version_tag

    if mode == "multi-paper":
        rows = load_paper_instruments(conn, include_crypto=True, tradfi_only=False)
        symbols = [r["canonical_asset"] for r in rows][:max_symbols]
        if not symbols:
            symbols = list(CORE_SYMBOLS)[:max_symbols]
            reason = "mode=multi-paper fallback=core (no paper_enabled instruments)"
        else:
            reason = f"mode=multi-paper paper_enabled={len(symbols)}"
        version_tag = f"multi-paper-{int(time.time())}"
        _log_universe(conn, version_tag, symbols, reason)
        return symbols, version_tag

    if mode == "multi":
        rows = load_active_instruments(conn, tiers=("CORE", "LIQUID"))
        symbols = [r["canonical_asset"] for r in rows][:max_symbols]
        if not symbols:
            symbols = list(CORE_SYMBOLS)[:max_symbols]
            reason = "mode=multi fallback=core (run instrument-discover first)"
        else:
            reason = f"mode=multi legacy_active={len(symbols)}"
        version_tag = f"multi-{int(time.time())}"
        _log_universe(conn, version_tag, symbols, reason)
        return symbols, version_tag

    symbols = list(CORE_SYMBOLS)[:max_symbols]
    reason = f"mode={mode} unknown_fallback_core"
    version_tag = f"{mode}-{int(time.time())}"
    _log_universe(conn, version_tag, symbols, reason)
    return symbols, version_tag


def needs_multi_venue_feed(mode: str, explicit_symbols: list[str] | None = None) -> bool:
    if explicit_symbols:
        return True
    return mode in ("multi", "multi-paper", "tradfi-liquid")


def _log_universe(conn: Any, version_tag: str, symbols: list[str], reason: str) -> None:
    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_universe_log (version_tag, symbols_json, selection_reason, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (version_tag, json.dumps(symbols), reason, int(time.time())),
    )
