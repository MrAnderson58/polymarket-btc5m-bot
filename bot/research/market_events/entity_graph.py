"""Static news entity graph — architecture only, no AI classification."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EntityNode:
    entity_id: str
    canonical_asset: str
    entity_type: str
    aliases: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# Static registry (Task E — architecture only, not used for trade decisions in E.2)
ENTITY_GRAPH: dict[str, EntityNode] = {
    "TSLA": EntityNode("TSLA", "TSLA", "EQUITY", [
        "Tesla", "TSLA", "Elon Musk", "EV sales", "deliveries", "FSD", "autonomous driving",
    ]),
    "NVDA": EntityNode("NVDA", "NVDA", "EQUITY", [
        "Nvidia", "NVDA", "Jensen Huang", "AI chips", "GPU", "data center capex",
    ]),
    "AAPL": EntityNode("AAPL", "AAPL", "EQUITY", [
        "Apple", "AAPL", "iPhone", "Tim Cook", "App Store", "China sales",
    ]),
    "META": EntityNode("META", "META", "EQUITY", ["Meta", "Facebook", "META", "Zuckerberg"]),
    "AMZN": EntityNode("AMZN", "AMZN", "EQUITY", ["Amazon", "AMZN", "AWS"]),
    "MSFT": EntityNode("MSFT", "MSFT", "EQUITY", ["Microsoft", "MSFT", "Azure"]),
    "GOOGL": EntityNode("GOOGL", "GOOGL", "EQUITY", ["Google", "Alphabet", "GOOGL"]),
    "OIL": EntityNode("OIL", "OIL", "COMMODITY", [
        "OPEC", "OPEC+", "Saudi Arabia", "EIA", "IEA", "crude", "Strait of Hormuz",
    ]),
    "GOLD": EntityNode("GOLD", "GOLD", "COMMODITY", ["gold", "XAU", "precious metals"]),
    "SP500_PROXY": EntityNode("SP500_PROXY", "SP500_PROXY", "INDEX", [
        "S&P 500", "SPX", "CPI", "PPI", "NFP", "FOMC", "Federal Reserve", "Powell",
    ]),
    "NASDAQ100_PROXY": EntityNode("NASDAQ100_PROXY", "NASDAQ100_PROXY", "INDEX", [
        "Nasdaq", "QQQ", "tech sector", "rate cuts", "Treasury yields",
    ]),
}


import time


def seed_entity_registry(conn: Any) -> int:
    n = 0
    now = int(time.time())
    for node in ENTITY_GRAPH.values():
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO market_events_entity_registry (
                  entity_id, canonical_asset, entity_type, aliases_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    node.entity_id,
                    node.canonical_asset,
                    node.entity_type,
                    json.dumps(node.aliases),
                    json.dumps(node.metadata),
                    now,
                ),
            )
            n += 1
        except Exception:
            pass
    return n


def entities_for_asset(canonical_asset: str) -> list[EntityNode]:
    if canonical_asset in ENTITY_GRAPH:
        return [ENTITY_GRAPH[canonical_asset]]
    return []
