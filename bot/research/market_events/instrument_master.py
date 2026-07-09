"""Instrument master registry — CRUD and lookup."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id


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
              active=?, metadata_json=?, updated_at=?
            WHERE id=?
            """,
            (
                rec.canonical_asset, rec.reference_asset, rec.asset_class, rec.instrument_type,
                rec.quote_currency, rec.trading_hours_mode, rec.price_source,
                rec.reference_price_source, rec.contract_type, rec.funding_applicable,
                rec.leverage_available, rec.liquidity_tier, rec.active, meta, now, existing["id"],
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
          active, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.venue, rec.venue_symbol, rec.canonical_asset, rec.reference_asset,
            rec.asset_class, rec.instrument_type, rec.quote_currency, rec.trading_hours_mode,
            rec.price_source, rec.reference_price_source, rec.contract_type,
            rec.funding_applicable, rec.leverage_available, rec.liquidity_tier,
            rec.active, meta, now, now,
        ),
    )


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
