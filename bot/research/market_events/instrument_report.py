"""Instrument registry and discovery reporting."""

from __future__ import annotations

import json
from typing import Any


def instrument_registry_report(conn: Any) -> str:
    rows = conn.execute(
        """
        SELECT venue, venue_symbol, canonical_asset, reference_asset, asset_class,
               instrument_type, liquidity_tier, active, price_source, reference_price_source
        FROM market_events_instruments
        ORDER BY active DESC, asset_class, canonical_asset
        """,
    ).fetchall()
    last_run = conn.execute(
        "SELECT * FROM market_events_discovery_runs ORDER BY run_ts DESC LIMIT 1",
    ).fetchone()
    lines = [
        "INSTRUMENT REGISTRY REPORT",
        f"total_instruments: {len(rows)}",
        f"active: {sum(1 for r in rows if r['active'])}",
        "",
        "=== BY ASSET CLASS ===",
    ]
    by_class: dict[str, list] = {}
    for r in rows:
        by_class.setdefault(r["asset_class"], []).append(r)
    for cls, items in sorted(by_class.items()):
        lines.append(f"  {cls}: {len(items)} (active={sum(1 for i in items if i['active'])})")
    lines.extend(["", "=== PROPOSED PAPER MONITORING (active) ==="])
    for r in rows:
        if not r["active"]:
            continue
        lines.append(
            f"  {r['canonical_asset']} ({r['asset_class']}) "
            f"{r['venue']}:{r['venue_symbol']} tier={r['liquidity_tier']} "
            f"ref={r['reference_price_source']}",
        )
    lines.extend(["", "=== WATCH LIST (inactive, discovered) ==="])
    for r in rows:
        if r["active"]:
            continue
        lines.append(
            f"  {r['canonical_asset']} {r['venue']}:{r['venue_symbol']} tier={r['liquidity_tier']}",
        )
    if last_run:
        lines.extend(["", "=== LAST DISCOVERY RUN ==="])
        prov = json.loads(last_run["provenance_json"] or "{}")
        for k, v in prov.items():
            if isinstance(v, list):
                lines.append(f"  {k}:")
                for item in v:
                    lines.append(f"    - {item}")
            else:
                lines.append(f"  {k}: {v}")
    lines.extend([
        "",
        "=== RECOMMENDATION ===",
        "  Phase 1 paper: CORE crypto on Binance (universe=core) — unchanged E.1 path",
        "  Phase 2 paper: enable TradFi after instrument-discover --enable-tradfi + liquidity review",
        "  Do not pool crypto and equity headline expectancy",
    ])
    return "\n".join(lines)
