"""Signal Evolution Engine V1 — research-only signal lifecycle & drift.

Does not modify Paper / Strategy / Execution / Gate / Optimizer.
"""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.signal_evolution_v1.engine import (
    run_signal_evolution_v1,
    update_signal,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.library import (
    LIBRARY_TABLE,
    ensure_evolution_schema,
)

__all__ = [
    "LIBRARY_TABLE",
    "ensure_evolution_schema",
    "run_signal_evolution_v1",
    "update_signal",
]
