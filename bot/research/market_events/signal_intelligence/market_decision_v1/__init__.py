"""Market Decision Engine V1 — research-only composer of existing engines."""

from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    decide_one,
    format_decision,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.engine import (
    run_market_decision_v1,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.explain import (
    explain_from_decision,
    explain_trade,
    format_explain,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.report import (
    format_terminal,
)

__all__ = [
    "decide_one",
    "explain_from_decision",
    "explain_trade",
    "format_decision",
    "format_explain",
    "format_terminal",
    "run_market_decision_v1",
]
