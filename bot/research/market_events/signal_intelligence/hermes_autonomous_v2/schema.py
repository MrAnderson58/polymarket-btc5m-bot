"""Schema for Hermes Autonomous Research V2."""

from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package import (
    SCHEMA_DDL,
    SCHEMA_TABLE,
    ensure_hermes_autonomous_schema,
)

__all__ = ["SCHEMA_DDL", "SCHEMA_TABLE", "ensure_hermes_autonomous_schema"]
