"""Market Causality Engine V1 — causal attribution for CLOSED trades.

Research-only. Does not modify Gate / Strategy / Paper / Optimizer / Execution.
"""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.market_causality_v1.engine import (
    run_market_causality_v1,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.library import (
    LIBRARY_TABLE,
    ensure_causality_library_schema,
)

__all__ = [
    "LIBRARY_TABLE",
    "ensure_causality_library_schema",
    "run_market_causality_v1",
]
