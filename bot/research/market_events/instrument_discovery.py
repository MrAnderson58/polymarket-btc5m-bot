"""Discover instruments from venue APIs — provenance stored, no invented mappings."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import CORE_SYMBOLS
from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.entity_graph import seed_entity_registry
from bot.research.market_events.instrument_master import InstrumentRecord, upsert_instrument
from bot.research.market_events.instrument_types import (
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_INDEX,
    BYBIT_COMMODITY_MAP,
    BYBIT_INDEX_MAP,
    COMMODITY_CANDIDATES,
    CRYPTO_CANDIDATES,
    EQUITY_CANDIDATES,
    HOURS_COMMODITY,
    HOURS_CRYPTO_24H,
    HOURS_US_EQUITY,
    INST_TYPE_PERPETUAL,
    INST_TYPE_SYNTHETIC_PERPETUAL,
    TIER_CORE,
    TIER_INACTIVE,
    TIER_LIQUID,
    TIER_WATCH,
    VENUE_BINANCE_FUTURES,
    VENUE_BYBIT_LINEAR,
)
from bot.research.market_events.venue_binance_discovery import BinanceDiscoveryClient
from bot.research.market_events.venue_bybit import BybitMarketClient

logger = logging.getLogger(__name__)

MIN_TURNOVER_USD_CRYPTO = 50_000_000
MIN_TURNOVER_USD_TRADFI = 1_000_000


@dataclass
class DiscoveryResult:
    binance_count: int = 0
    bybit_stock_count: int = 0
    bybit_commodity_count: int = 0
    bybit_index_count: int = 0
    enabled_count: int = 0
    proposed_universe: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)


def _classify_binance(inst) -> InstrumentRecord | None:
    base = inst.base_asset.upper()
    if base not in CRYPTO_CANDIDATES and base not in CORE_SYMBOLS:
        return None
    tier = TIER_CORE if base in CORE_SYMBOLS else TIER_LIQUID
    return InstrumentRecord(
        venue=VENUE_BINANCE_FUTURES,
        venue_symbol=inst.venue_symbol,
        canonical_asset=base,
        reference_asset=base,
        asset_class=ASSET_CLASS_CRYPTO,
        instrument_type=INST_TYPE_PERPETUAL,
        trading_hours_mode=HOURS_CRYPTO_24H,
        price_source="binance_futures_last",
        reference_price_source="binance_futures_last",
        contract_type=inst.contract_type,
        funding_applicable=1,
        liquidity_tier=tier,
        active=1 if tier == TIER_CORE else 0,
        metadata={"discovered_from": "binance_exchangeInfo"},
    )


def _classify_bybit_stock(inst) -> InstrumentRecord | None:
    sym = inst.venue_symbol.upper()
    base = inst.base_coin.upper() or sym.replace("USDT", "")
    if base not in EQUITY_CANDIDATES:
        return None
    return InstrumentRecord(
        venue=VENUE_BYBIT_LINEAR,
        venue_symbol=sym,
        canonical_asset=base,
        reference_asset=base,
        asset_class=ASSET_CLASS_EQUITY,
        instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL,
        trading_hours_mode=HOURS_US_EQUITY,
        price_source="bybit_last",
        reference_price_source="bybit_index",
        contract_type=inst.contract_type,
        funding_applicable=1,
        liquidity_tier=TIER_WATCH,
        active=0,
        metadata={"discovered_from": "bybit_instruments_info", "symbolType": inst.symbol_type},
    )


def _classify_bybit_commodity(inst) -> InstrumentRecord | None:
    sym = inst.venue_symbol.upper()
    canonical = BYBIT_COMMODITY_MAP.get(sym)
    if not canonical or canonical not in COMMODITY_CANDIDATES:
        return None
    return InstrumentRecord(
        venue=VENUE_BYBIT_LINEAR,
        venue_symbol=sym,
        canonical_asset=canonical,
        reference_asset=canonical,
        asset_class=ASSET_CLASS_COMMODITY,
        instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL,
        trading_hours_mode=HOURS_COMMODITY,
        price_source="bybit_last",
        reference_price_source="bybit_index",
        contract_type=inst.contract_type,
        funding_applicable=1,
        liquidity_tier=TIER_WATCH,
        active=0,
        metadata={"discovered_from": "bybit_instruments_info", "symbolType": "commodity"},
    )


def _classify_bybit_index(inst) -> InstrumentRecord | None:
    sym = inst.venue_symbol.upper()
    canonical = BYBIT_INDEX_MAP.get(sym)
    if not canonical:
        return None
    return InstrumentRecord(
        venue=VENUE_BYBIT_LINEAR,
        venue_symbol=sym,
        canonical_asset=canonical,
        reference_asset=canonical,
        asset_class=ASSET_CLASS_INDEX,
        instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL,
        trading_hours_mode=HOURS_US_EQUITY,
        price_source="bybit_last",
        reference_price_source="bybit_index",
        contract_type=inst.contract_type,
        funding_applicable=1,
        liquidity_tier=TIER_WATCH,
        active=0,
        metadata={"discovered_from": "bybit_instruments_info", "symbolType": "index"},
    )


def _apply_liquidity_tier(rec: InstrumentRecord, turnover_24h: float) -> InstrumentRecord:
    min_turn = MIN_TURNOVER_USD_TRADFI if rec.asset_class != ASSET_CLASS_CRYPTO else MIN_TURNOVER_USD_CRYPTO
    if turnover_24h < min_turn:
        rec.liquidity_tier = TIER_INACTIVE
        rec.active = 0
    elif rec.liquidity_tier == TIER_WATCH and turnover_24h >= min_turn * 2:
        rec.liquidity_tier = TIER_LIQUID
    return rec


def run_instrument_discovery(conn: Any, *, enable_tradfi: bool = False) -> DiscoveryResult:
    result = DiscoveryResult()
    result.limitations.append("Binance: crypto USDT perpetuals only in E.2")
    result.limitations.append("Bybit TradFi may return stale prices outside US session hours")
    result.limitations.append("No external Yahoo/refinitiv reference feed — Bybit indexPrice used for basis")
    result.limitations.append("xStock spot tokens (TSLAXUSDT) not auto-enabled; stock perps preferred")

    binance = BinanceDiscoveryClient()
    bybit = BybitMarketClient()

    for inst in binance.fetch_perpetual_instruments():
        rec = _classify_binance(inst)
        if not rec:
            continue
        ticker = binance.fetch_ticker_24h(inst.venue_symbol)
        turnover = float(ticker.get("quoteVolume", 0) or 0) if ticker else 0
        rec = _apply_liquidity_tier(rec, turnover)
        upsert_instrument(conn, rec)
        result.binance_count += 1

    for fetch_fn, counter_attr in (
        (lambda: bybit.fetch_instruments(category="linear", symbol_type="stock"), "bybit_stock_count"),
        (lambda: bybit.fetch_instruments(category="linear", symbol_type="commodity"), "bybit_commodity_count"),
    ):
        try:
            instruments = fetch_fn()
        except Exception as exc:
            result.limitations.append(f"Bybit fetch failed: {exc}")
            instruments = []
        for inst in instruments:
            if counter_attr == "bybit_stock_count":
                rec = _classify_bybit_stock(inst)
            else:
                rec = _classify_bybit_commodity(inst)
            if not rec:
                continue
            ticker = bybit.fetch_ticker(inst.venue_symbol)
            turnover = ticker.turnover_24h if ticker else 0
            rec = _apply_liquidity_tier(rec, turnover)
            if enable_tradfi and rec.liquidity_tier in (TIER_LIQUID, TIER_CORE):
                rec.active = 1
            setattr(result, counter_attr, getattr(result, counter_attr) + 1)
            upsert_instrument(conn, rec)

    try:
        linear_all = bybit.fetch_instruments(category="linear")
        for inst in linear_all:
            rec = _classify_bybit_index(inst)
            if rec:
                ticker = bybit.fetch_ticker(inst.venue_symbol)
                rec = _apply_liquidity_tier(rec, ticker.turnover_24h if ticker else 0)
                if enable_tradfi and rec.liquidity_tier == TIER_LIQUID:
                    rec.active = 1
                upsert_instrument(conn, rec)
                result.bybit_index_count += 1
    except Exception as exc:
        result.limitations.append(f"Bybit index scan: {exc}")

    seed_entity_registry(conn)

    active = conn.execute(
        "SELECT canonical_asset, asset_class, venue, liquidity_tier FROM market_events_instruments WHERE active=1",
    ).fetchall()
    result.enabled_count = len(active)
    result.proposed_universe = [r["canonical_asset"] for r in active]

    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_discovery_runs (venue, discovered_count, run_ts, provenance_json)
        VALUES (?, ?, ?, ?)
        """,
        ("multi", result.binance_count + result.bybit_stock_count + result.bybit_commodity_count,
         int(time.time()), json.dumps({
             "binance": result.binance_count,
             "bybit_stock": result.bybit_stock_count,
             "bybit_commodity": result.bybit_commodity_count,
             "bybit_index": result.bybit_index_count,
             "enabled": result.enabled_count,
             "limitations": result.limitations,
         })),
    )
    return result
