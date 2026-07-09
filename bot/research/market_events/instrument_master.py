"""Instrument master registry — CRUD and lookup."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.instrument_types import ACTIVATION_INACTIVE
from bot.research.market_events.reference_provider import REFERENCE_PROVIDER_SELF


@dataclass
class InstrumentRecord:
    venue: str
    venue_symbol: str
    canonical_asset: str
    reference_asset: str
    asset_class: str
    instrument_type: str
    quote_currency: str = "USDT"
    trading_hours_mode: str = "CRYPTO_24H"
    price_source: str = ""
    reference_price_source: str = ""
    contract_type: str = ""
    funding_applicable: int = 0
    leverage_available: int = 1
    liquidity_tier: str = "WATCH"
    active: int = 0
    activation_tier: str = ACTIVATION_INACTIVE
    observe_enabled: int = 0
    paper_enabled: int = 0
    reference_provider: str = ""
    metadata: dict | None = None

    @property
    def instrument_key(self) -> str:
        return f"{self.venue}:{self.venue_symbol}"


def upsert_instrument(conn: Any, rec: InstrumentRecord) -> int:
    existing = conn.execute(
        "SELECT id FROM market_events_instruments WHERE venue = ? AND venue_symbol = ?",
        (rec.venue, rec.venue_symbol),
    ).fetchone()
    meta = json.dumps(rec.metadata or {})
    now = int(time.time())
    if existing:
        conn.execute(
            """
            UPDATE market_events_instruments SET
              canonical_asset=?, reference_asset=?, asset_class=?, instrument_type=?,
              quote_currency=?, trading_hours_mode=?, price_source=?, reference_price_source=?,
              contract_type=?, funding_applicable=?, leverage_available=?, liquidity_tier=?,
              active=?, activation_tier=?, observe_enabled=?, paper_enabled=?,
              reference_provider=?, metadata_json=?, updated_at=?
            WHERE id=?
            """,
            (
                rec.canonical_asset, rec.reference_asset, rec.asset_class, rec.instrument_type,
                rec.quote_currency, rec.trading_hours_mode, rec.price_source,
                rec.reference_price_source, rec.contract_type, rec.funding_applicable,
                rec.leverage_available, rec.liquidity_tier, rec.active,
                rec.activation_tier, rec.observe_enabled, rec.paper_enabled,
                rec.reference_provider or rec.reference_price_source, meta, now, existing["id"],
            ),
        )
        return int(existing["id"])
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_instruments (
          venue, venue_symbol, canonical_asset, reference_asset, asset_class, instrument_type,
          quote_currency, trading_hours_mode, price_source, reference_price_source,
          contract_type, funding_applicable, leverage_available, liquidity_tier,
          active, activation_tier, observe_enabled, paper_enabled, reference_provider,
          metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.venue, rec.venue_symbol, rec.canonical_asset, rec.reference_asset,
            rec.asset_class, rec.instrument_type, rec.quote_currency, rec.trading_hours_mode,
            rec.price_source, rec.reference_price_source, rec.contract_type,
            rec.funding_applicable, rec.leverage_available, rec.liquidity_tier,
            rec.active, rec.activation_tier, rec.observe_enabled, rec.paper_enabled,
            rec.reference_provider or rec.reference_price_source, meta, now, now,
        ),
    )


def load_paper_instruments(
    conn: Any,
    *,
    include_crypto: bool = True,
    tradfi_only: bool = False,
) -> list[dict[str, Any]]:
    clauses = ["paper_enabled = 1"]
    if tradfi_only:
        clauses.append("asset_class != 'CRYPTO'")
    elif not include_crypto:
        clauses.append("asset_class = 'CRYPTO'")
    where = " AND ".join(clauses)
    return conn.execute(
        f"""
        SELECT * FROM market_events_instruments
        WHERE {where}
        ORDER BY asset_class, canonical_asset
        """,
    ).fetchall()


def load_observe_instruments(conn: Any) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT * FROM market_events_instruments
        WHERE observe_enabled = 1
        ORDER BY activation_tier DESC, asset_class, canonical_asset
        """,
    ).fetchall()


def resolve_instruments_by_symbols(conn: Any, symbols: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in symbols:
        s = raw.strip().upper()
        if not s:
            continue
        row = conn.execute(
            """
            SELECT * FROM market_events_instruments
            WHERE canonical_asset = ? OR venue_symbol = ?
            ORDER BY paper_enabled DESC, observe_enabled DESC
            LIMIT 1
            """,
            (s, s),
        ).fetchone()
        if row:
            out.append(dict(row))
    return out


def load_active_instruments(conn: Any, *, tiers: tuple[str, ...] = ("CORE", "LIQUID")) -> list[dict[str, Any]]:
    ph = ",".join("?" for _ in tiers)
    return conn.execute(
        f"""
        SELECT * FROM market_events_instruments
        WHERE active = 1 AND liquidity_tier IN ({ph})
        ORDER BY asset_class, canonical_asset
        """,
        list(tiers),
    ).fetchall()


def load_instrument_by_symbol(conn: Any, symbol: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM market_events_instruments
        WHERE canonical_asset = ? AND active = 1
        ORDER BY liquidity_tier LIMIT 1
        """,
        (symbol.upper(),),
    ).fetchone()
    return row
