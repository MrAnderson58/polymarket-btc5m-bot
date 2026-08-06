"""Research Integrity Fix V1 — ONE canonical dataset for all reports."""

from bot.research.market_events.signal_intelligence.research_integrity_v1.canonical import (
    load_canonical_elite,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.engine import (
    run_research_integrity_v1,
)

__all__ = [
    "load_canonical_elite",
    "run_research_integrity_v1",
]
