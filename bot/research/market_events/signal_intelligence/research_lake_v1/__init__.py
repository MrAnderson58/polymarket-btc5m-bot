"""Research Lake Builder V1 — canonical trade-centric research store.

Research-only. Does not modify Gate / Optimizer / Strategy / Paper / Execution.
"""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.research_lake_v1.builder import (
    build_research_lake_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.health import (
    research_lake_health_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    load_research_lake_rows,
    research_lake_row_count,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.materialize import (
    materialize_closed_from_s40_reviews,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.s55_join import (
    diagnose_missing_s55_joins,
    format_s55_join_audit,
    repair_s55_joins,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    DATASET_VERSION,
    FEATURE_VERSION,
    SCHEMA_VERSION_LAKE,
    ensure_research_lake_schema,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.sync import (
    lake_lag,
    sync_closed_trade_to_lake,
    sync_research_lake_incremental,
)

__all__ = [
    "DATASET_VERSION",
    "FEATURE_VERSION",
    "SCHEMA_VERSION_LAKE",
    "build_research_lake_v1",
    "diagnose_missing_s55_joins",
    "ensure_research_lake_schema",
    "format_s55_join_audit",
    "lake_lag",
    "load_research_lake_rows",
    "materialize_closed_from_s40_reviews",
    "repair_s55_joins",
    "research_lake_health_v1",
    "research_lake_row_count",
    "sync_closed_trade_to_lake",
    "sync_research_lake_incremental",
]
