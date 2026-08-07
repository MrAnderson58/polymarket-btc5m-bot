"""Hermes Autonomous Research V2 — package-only daily research (research-only)."""

from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.autostart import (
    audit_autostart,
)
from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes import (
    run_daily_hermes_report,
)
from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package import (
    run_daily_research_package,
)

__all__ = [
    "audit_autostart",
    "run_daily_hermes_report",
    "run_daily_research_package",
]
