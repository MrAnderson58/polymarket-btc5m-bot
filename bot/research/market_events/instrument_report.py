"""Instrument registry and discovery reporting."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.reference_provider import reference_audit_report


def instrument_registry_report(conn: Any) -> str:
    rows = conn.execute(
        """
        SELECT venue, venue_symbol, canonical_asset, asset_class, liquidity_tier,
               activation_tier, observe_enabled, paper_enabled, active,
               reference_provider, reference_price_source
        FROM market_events_instruments
        ORDER BY paper_enabled DESC, observe_enabled DESC, asset_class, canonical_asset
        """,
    ).fetchall()
    last_run = conn.execute(
        "SELECT * FROM market_events_discovery_runs ORDER BY run_ts DESC LIMIT 1",
    ).fetchone()
    lines = [
        "INSTRUMENT REGISTRY REPORT",
        f"total_instruments: {len(rows)}",
        f"paper_active: {sum(1 for r in rows if r['paper_enabled'])}",
        f"watch_observe: {sum(1 for r in rows if r['activation_tier'] == 'WATCH')}",
        f"inactive: {sum(1 for r in rows if r['activation_tier'] == 'INACTIVE')}",
        "",
        "=== BY ACTIVATION TIER ===",
    ]
    for tier in ("PAPER_ACTIVE", "WATCH", "INACTIVE"):
        items = [r for r in rows if r["activation_tier"] == tier]
        lines.append(f"  {tier}: {len(items)}")
        for r in items:
            lines.append(
                f"    {r['canonical_asset']} {r['venue']}:{r['venue_symbol']} "
                f"liq={r['liquidity_tier']} observe={r['observe_enabled']} paper={r['paper_enabled']}",
            )
    if last_run:
        lines.extend(["", "=== LAST DISCOVERY RUN ==="])
        prov = json.loads(last_run["provenance_json"] or "{}")
        for k in ("binance", "bybit_stock", "bybit_commodity", "bybit_index_legacy", "bybit_etf_proxy", "paper_active", "watch"):
            if k in prov:
                lines.append(f"  {k}: {prov[k]}")
        idx = prov.get("index_audit") or {}
        if idx:
            lines.extend(["", "  index_audit:"])
            for miss in idx.get("legacy_map_misses") or []:
                lines.append(f"    legacy_miss: {miss}")
            for hit in idx.get("etf_proxy_hits") or []:
                lines.append(f"    etf_proxy: {hit.get('venue_symbol')} -> {hit.get('canonical_asset')}")
            for finding in idx.get("findings") or []:
                lines.append(f"    - {finding}")
    ref = reference_audit_report()
    lines.extend([
        "",
        "=== REFERENCE PRICE QUALITY ===",
        f"  tradfi_provider: {ref['current_tradfi_provider']}",
        f"  external_market_basis: {ref['external_market_basis']}",
        f"  note: {ref['dislocation_interpretation']}",
        "",
        "=== RECOMMENDATION ===",
        "  observe-run: WATCH + PAPER_ACTIVE (no paper trades for WATCH)",
        "  shock-paper-run --universe tradfi-liquid: PAPER_ACTIVE TradFi only",
        "  shock-paper-run --universe multi-paper: crypto + TradFi PAPER_ACTIVE",
        "  Do not pool crypto and equity headline expectancy",
    ])
    return "\n".join(lines)
