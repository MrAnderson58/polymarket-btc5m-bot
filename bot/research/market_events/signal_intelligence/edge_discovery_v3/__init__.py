"""Market Edge Discovery V3 — combinatorial / Bayesian / regime research engine.

Research-only. Does not modify Gate / Strategy / Optimizer / Paper / Execution.
"""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.edge_discovery_v3.engine import (
    run_edge_discovery_v3,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.library import (
    LIBRARY_TABLE,
    ensure_edge_library_schema,
)

__all__ = [
    "LIBRARY_TABLE",
    "ensure_edge_library_schema",
    "run_edge_discovery_v3",
]
