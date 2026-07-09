"""Discover instruments from venue APIs — provenance stored, no invented mappings."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.activation_rules import (
    DEFAULT_RULES,
    evaluate_activation,
    merge_activation_metadata,
)
from bot.research.market_events.config import CORE_SYMBOLS
from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.entity_graph import seed_entity_registry
from bot.research.market_events.index_discovery_audit import audit_bybit_index_discovery
from bot.research.market_events.instrument_master import InstrumentRecord, upsert_instrument
from bot.research.market_events.instrument_types import (
    ACTIVATION_INACTIVE,
    ACTIVATION_PAPER_ACTIVE,
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_ETF,
    ASSET_CLASS_INDEX,
    BYBIT_COMMODITY_MAP,
    BYBIT_ETF_PROXY_MAP,
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
from bot.research.market_events.reference_provider import (
    REFERENCE_PROVIDER_BYBIT_INDEX,
    REFERENCE_PROVIDER_SELF,
)
from bot.research.market_events.session_regime import classify_session_regime
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
    bybit_etf_proxy_count: int = 0
    enabled_count: int = 0
    paper_active_count: int = 0
    watch_count: int = 0
    proposed_universe: list[str] = field(default_factory=list)
    proposed_paper: list[str] = field(default_factory=list)
    proposed_observe: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    index_audit: dict[str, Any] = field(default_factory=dict)


def _classify_binance(inst) -> InstrumentRecord | None:
    base = inst.base_asset.upper()
    if base not in CRYPTO_CANDIDATES and base not in CORE_SYMBOLS:
        return None
    tier = TIER_CORE if base in CORE_SYMBOLS else TIER_LIQUID
    paper = 1 if tier == TIER_CORE else 0
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
        reference_provider=REFERENCE_PROVIDER_SELF,
        contract_type=inst.contract_type,
        funding_applicable=1,
        liquidity_tier=tier,
        active=paper,
        activation_tier=ACTIVATION_PAPER_ACTIVE if paper else ACTIVATION_INACTIVE,
        observe_enabled=paper,
        paper_enabled=paper,
        metadata={"discovered_from": "binance_exchangeInfo"},
    )


def _classify_bybit_stock(inst) -> InstrumentRecord | None:
    sym = inst.venue_symbol.upper()
    if sym in BYBIT_ETF_PROXY_MAP:
        return None
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
        reference_provider=REFERENCE_PROVIDER_BYBIT_INDEX,
        contract_type=inst.contract_type,
        funding_applicable=1,
        liquidity_tier=TIER_WATCH,
        active=0,
        activation_tier=ACTIVATION_INACTIVE,
        observe_enabled=0,
        paper_enabled=0,
        metadata={"discovered_from": "bybit_instruments_info", "symbolType": inst.symbol_type},
    )


def _classify_bybit_etf_proxy(inst) -> InstrumentRecord | None:
    sym = inst.venue_symbol.upper()
    canonical = BYBIT_ETF_PROXY_MAP.get(sym)
    if not canonical:
        return None
    return InstrumentRecord(
        venue=VENUE_BYBIT_LINEAR,
        venue_symbol=sym,
        canonical_asset=canonical,
        reference_asset=canonical,
        asset_class=ASSET_CLASS_ETF,
        instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL,
        trading_hours_mode=HOURS_US_EQUITY,
        price_source="bybit_last",
        reference_price_source="bybit_index",
        reference_provider=REFERENCE_PROVIDER_BYBIT_INDEX,
        contract_type=inst.contract_type,
        funding_applicable=1,
        liquidity_tier=TIER_WATCH,
        active=0,
        activation_tier=ACTIVATION_INACTIVE,
        observe_enabled=0,
        paper_enabled=0,
        metadata={
            "discovered_from": "bybit_instruments_info",
            "symbolType": inst.symbol_type,
            "note": "ETF stock perp proxy — not US500USDT/US100USDT",
        },
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
        reference_provider=REFERENCE_PROVIDER_BYBIT_INDEX,
        contract_type=inst.contract_type,
        funding_applicable=1,
        liquidity_tier=TIER_WATCH,
        active=0,
        activation_tier=ACTIVATION_INACTIVE,
        observe_enabled=0,
        paper_enabled=0,
        metadata={"discovered_from": "bybit_instruments_info", "symbolType": "commodity"},
    )


def _apply_liquidity_tier(rec: InstrumentRecord, turnover_24h: float) -> InstrumentRecord:
    min_turn = MIN_TURNOVER_USD_TRADFI if rec.asset_class != ASSET_CLASS_CRYPTO else MIN_TURNOVER_USD_CRYPTO
    if turnover_24h < min_turn:
        rec.liquidity_tier = TIER_INACTIVE
    elif rec.liquidity_tier == TIER_WATCH and turnover_24h >= min_turn * 2:
        rec.liquidity_tier = TIER_LIQUID
    return rec


def _apply_activation(
    conn: Any,
    rec: InstrumentRecord,
    *,
    ticker,
    enable_tradfi: bool,
    poll_ts: int,
) -> InstrumentRecord:
    if rec.asset_class == ASSET_CLASS_CRYPTO:
        return rec
    existing = conn.execute(
        "SELECT metadata_json FROM market_events_instruments WHERE venue=? AND venue_symbol=?",
        (rec.venue, rec.venue_symbol),
    ).fetchone()
    prior_meta = json.loads(existing["metadata_json"]) if existing and existing["metadata_json"] else {}
    session = classify_session_regime(
        poll_ts, asset_class=rec.asset_class, trading_hours_mode=rec.trading_hours_mode,
    )
    decision = evaluate_activation(
        asset_class=rec.asset_class,
        ticker=ticker,
        session_regime=session,
        prior_metadata=prior_meta,
        poll_ts=poll_ts,
        allow_paper_promotion=enable_tradfi,
    )
    rec.activation_tier = decision.tier
    rec.observe_enabled = decision.observe_enabled
    rec.paper_enabled = decision.paper_enabled
    rec.active = decision.paper_enabled
    rec.metadata = json.loads(merge_activation_metadata(prior_meta, decision))
    return rec


def run_instrument_discovery(conn: Any, *, enable_tradfi: bool = False) -> DiscoveryResult:
    result = DiscoveryResult()
    poll_ts = int(time.time())
    result.limitations.append("Binance: crypto USDT perpetuals only")
    result.limitations.append("Bybit TradFi reference=indexPrice — same venue, not external market")
    result.limitations.append("enable_tradfi applies activation rules; does not blind-activate all LIQUID")

    binance = BinanceDiscoveryClient()
    bybit = BybitMarketClient()

    index_audit = audit_bybit_index_discovery(bybit)
    result.bybit_index_count = index_audit.bybit_index_count
    result.bybit_etf_proxy_count = index_audit.etf_proxy_count
    result.index_audit = {
        "legacy_map_misses": index_audit.legacy_map_misses,
        "legacy_map_hits": index_audit.legacy_map_hits,
        "symbol_type_index_count": index_audit.symbol_type_index_count,
        "etf_proxy_hits": index_audit.etf_proxy_hits,
        "findings": index_audit.findings,
        "spx_meme_warning": index_audit.spx_meme_warning,
    }
    result.limitations.extend(index_audit.findings)

    for inst in binance.fetch_perpetual_instruments():
        rec = _classify_binance(inst)
        if not rec:
            continue
        ticker = binance.fetch_ticker_24h(inst.venue_symbol)
        turnover = float(ticker.get("quoteVolume", 0) or 0) if ticker else 0
        rec = _apply_liquidity_tier(rec, turnover)
        upsert_instrument(conn, rec)
        result.binance_count += 1

    stock_instruments: list = []
    try:
        stock_instruments = bybit.fetch_instruments(category="linear", symbol_type="stock")
    except Exception as exc:
        result.limitations.append(f"Bybit stock fetch failed: {exc}")

    for inst in stock_instruments:
        for classify_fn, counter in (
            (_classify_bybit_etf_proxy, "bybit_etf_proxy_count"),
            (_classify_bybit_stock, "bybit_stock_count"),
        ):
            rec = classify_fn(inst)
            if not rec:
                continue
            ticker = bybit.fetch_ticker(inst.venue_symbol)
            turnover = ticker.turnover_24h if ticker else 0
            rec = _apply_liquidity_tier(rec, turnover)
            rec = _apply_activation(conn, rec, ticker=ticker, enable_tradfi=enable_tradfi, poll_ts=poll_ts)
            upsert_instrument(conn, rec)
            setattr(result, counter, getattr(result, counter) + 1)

    try:
        commodities = bybit.fetch_instruments(category="linear", symbol_type="commodity")
    except Exception as exc:
        result.limitations.append(f"Bybit commodity fetch failed: {exc}")
        commodities = []

    for inst in commodities:
        rec = _classify_bybit_commodity(inst)
        if not rec:
            continue
        ticker = bybit.fetch_ticker(inst.venue_symbol)
        rec = _apply_liquidity_tier(rec, ticker.turnover_24h if ticker else 0)
        rec = _apply_activation(conn, rec, ticker=ticker, enable_tradfi=enable_tradfi, poll_ts=poll_ts)
        upsert_instrument(conn, rec)
        result.bybit_commodity_count += 1

    seed_entity_registry(conn)

    rows = conn.execute(
        """
        SELECT canonical_asset, activation_tier, observe_enabled, paper_enabled, asset_class
        FROM market_events_instruments
        """,
    ).fetchall()
    result.paper_active_count = sum(1 for r in rows if r["paper_enabled"])
    result.watch_count = sum(1 for r in rows if r["activation_tier"] == "WATCH")
    result.enabled_count = result.paper_active_count
    result.proposed_paper = [r["canonical_asset"] for r in rows if r["paper_enabled"]]
    result.proposed_observe = [r["canonical_asset"] for r in rows if r["observe_enabled"]]
    result.proposed_universe = result.proposed_paper

    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_discovery_runs (venue, discovered_count, run_ts, provenance_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            "multi",
            result.binance_count + result.bybit_stock_count + result.bybit_commodity_count,
            poll_ts,
            json.dumps({
                "binance": result.binance_count,
                "bybit_stock": result.bybit_stock_count,
                "bybit_commodity": result.bybit_commodity_count,
                "bybit_index_legacy": result.bybit_index_count,
                "bybit_etf_proxy": result.bybit_etf_proxy_count,
                "paper_active": result.paper_active_count,
                "watch": result.watch_count,
                "index_audit": result.index_audit,
                "limitations": result.limitations,
            }),
        ),
    )
    return result
