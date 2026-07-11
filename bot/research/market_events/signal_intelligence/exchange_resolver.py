"""Exchange symbol resolver — Bybit > Binance > OKX."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import RESOLVER_VENUES


@dataclass(frozen=True)
class ExchangeResolution:
    canonical_symbol: str
    resolved_venue: str | None
    resolved_symbol: str | None
    status: str
    attempts: list[dict[str, Any]] = field(default_factory=list)


def _binance_symbol(canonical: str) -> str:
    return f"{canonical.upper()}USDT"


def _bybit_symbol(canonical: str) -> str:
    return f"{canonical.upper()}USDT"


def _try_bybit(canonical: str) -> tuple[bool, str | None]:
    try:
        from bot.research.market_events.venue_bybit import BybitMarketClient
        sym = _bybit_symbol(canonical)
        ticker = BybitMarketClient().fetch_ticker(sym)
        if ticker and ticker.last_price > 0:
            return True, sym
    except Exception as exc:
        return False, str(exc)
    return False, None


def _try_binance(canonical: str) -> tuple[bool, str | None]:
    try:
        from bot.research.market_events.venue_binance_discovery import BinanceDiscoveryClient
        sym = _binance_symbol(canonical)
        client = BinanceDiscoveryClient()
        info = client.fetch_exchange_info()
        symbols = {s.get("symbol") for s in info.get("symbols", []) if isinstance(s, dict)}
        if sym in symbols:
            return True, sym
    except Exception as exc:
        return False, str(exc)
    return False, None


def _try_okx(canonical: str) -> tuple[bool, str | None]:
    try:
        from bot.research.market_events.signal_intelligence.okx_client import check_symbol_exists
        if check_symbol_exists(canonical):
            return True, f"{canonical.upper()}-USDT-SWAP"
    except Exception as exc:
        return False, str(exc)
    return False, None


_VENUE_CHECKERS: dict[str, Callable[[str], tuple[bool, str | None]]] = {
    "bybit": _try_bybit,
    "binance": _try_binance,
    "okx": _try_okx,
}


def resolve_exchange_symbol(
    canonical: str,
    *,
    checkers: dict[str, Callable[[str], tuple[bool, str | None]]] | None = None,
) -> ExchangeResolution:
    """Priority: Bybit → Binance → OKX."""
    checkers = checkers or _VENUE_CHECKERS
    attempts: list[dict[str, Any]] = []
    for venue in RESOLVER_VENUES:
        fn = checkers.get(venue)
        if not fn:
            continue
        ok, detail = fn(canonical)
        attempts.append({"venue": venue, "found": ok, "detail": detail})
        if ok:
            if venue == "bybit":
                sym = _bybit_symbol(canonical)
            elif venue == "binance":
                sym = _binance_symbol(canonical)
            else:
                sym = f"{canonical.upper()}-USDT-SWAP"
            if isinstance(detail, str) and "USDT" in detail and not detail.startswith("Traceback"):
                sym = detail
            return ExchangeResolution(
                canonical_symbol=canonical,
                resolved_venue=venue,
                resolved_symbol=sym,
                status="RESOLVED",
                attempts=attempts,
            )
    return ExchangeResolution(
        canonical_symbol=canonical,
        resolved_venue=None,
        resolved_symbol=None,
        status="UNSUPPORTED_SYMBOL",
        attempts=attempts,
    )


def persist_resolution(conn: Any, resolution: ExchangeResolution) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_event_exchange_symbols (
          canonical_symbol, resolved_venue, resolved_symbol, status,
          exchange_attempts_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_symbol) DO UPDATE SET
          resolved_venue = excluded.resolved_venue,
          resolved_symbol = excluded.resolved_symbol,
          status = excluded.status,
          exchange_attempts_json = excluded.exchange_attempts_json,
          updated_at = excluded.updated_at
        """,
        (
            resolution.canonical_symbol,
            resolution.resolved_venue,
            resolution.resolved_symbol,
            resolution.status,
            json.dumps(resolution.attempts),
            now,
        ),
    )


def get_or_resolve(conn: Any, canonical: str) -> ExchangeResolution:
    row = conn.execute(
        "SELECT * FROM market_event_exchange_symbols WHERE canonical_symbol = ?",
        (canonical,),
    ).fetchone()
    if row and row["status"] == "RESOLVED":
        return ExchangeResolution(
            canonical_symbol=canonical,
            resolved_venue=row["resolved_venue"],
            resolved_symbol=row["resolved_symbol"],
            status=row["status"],
            attempts=json.loads(row["exchange_attempts_json"] or "[]"),
        )
    resolution = resolve_exchange_symbol(canonical)
    persist_resolution(conn, resolution)
    return resolution
