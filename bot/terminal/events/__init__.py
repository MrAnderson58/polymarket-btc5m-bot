"""Terminal events package."""

from bot.terminal.events.event_bus import EventBus, get_event_bus, reset_event_bus
from bot.terminal.events.events import (
    MorningBriefGenerated,
    PortfolioViewed,
    PositionClosed,
    PositionOpened,
    PositionRequested,
    RiskChanged,
    SignalViewed,
    StopModified,
    TakeProfitModified,
)
from bot.terminal.events.handlers import register_default_handlers

__all__ = [
    "EventBus",
    "MorningBriefGenerated",
    "PortfolioViewed",
    "PositionClosed",
    "PositionOpened",
    "PositionRequested",
    "RiskChanged",
    "SignalViewed",
    "StopModified",
    "TakeProfitModified",
    "get_event_bus",
    "register_default_handlers",
    "reset_event_bus",
]
