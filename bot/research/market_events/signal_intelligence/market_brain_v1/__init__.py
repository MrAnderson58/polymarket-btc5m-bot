"""Adaptive Market Brain V1 — unified research opinion layer.

Research-only. Does not modify Paper / Execution / Strategy / Gate.
"""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.market_brain_v1.engine import (
    run_market_brain_v1,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.library import (
    LIBRARY_TABLE,
    ensure_brain_library_schema,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
    DEFAULT_WEIGHTS,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "LIBRARY_TABLE",
    "ensure_brain_library_schema",
    "run_market_brain_v1",
]
