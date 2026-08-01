"""Shadow Live Evaluation V1 — research-only brain vs production shadow.

No trade execution. Does not modify Paper / Execution / Gate / Strategy / Optimizer.
"""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.shadow_live_v1.engine import (
    run_shadow_live_v1,
    shadow_decide_one,
)
from bot.research.market_events.signal_intelligence.shadow_live_v1.library import (
    LIBRARY_TABLE,
    ensure_shadow_library_schema,
)

__all__ = [
    "LIBRARY_TABLE",
    "ensure_shadow_library_schema",
    "run_shadow_live_v1",
    "shadow_decide_one",
]
