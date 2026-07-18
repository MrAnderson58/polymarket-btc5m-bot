"""Event handlers — stubs only (no business logic in V6.0.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.terminal.events import events as ev

if TYPE_CHECKING:
    from bot.terminal.events.event_bus import EventBus


def on_signal_viewed(event: ev.SignalViewed) -> None:
    pass


def on_position_requested(event: ev.PositionRequested) -> None:
    pass


def on_position_opened(event: ev.PositionOpened) -> None:
    pass


def on_position_closed(event: ev.PositionClosed) -> None:
    pass


def on_risk_changed(event: ev.RiskChanged) -> None:
    pass


def on_stop_modified(event: ev.StopModified) -> None:
    pass


def on_take_profit_modified(event: ev.TakeProfitModified) -> None:
    pass


def on_portfolio_viewed(event: ev.PortfolioViewed) -> None:
    pass


def on_morning_brief_generated(event: ev.MorningBriefGenerated) -> None:
    pass


def register_default_handlers(bus: EventBus) -> None:
    bus.subscribe(ev.SignalViewed, on_signal_viewed)
    bus.subscribe(ev.PositionRequested, on_position_requested)
    bus.subscribe(ev.PositionOpened, on_position_opened)
    bus.subscribe(ev.PositionClosed, on_position_closed)
    bus.subscribe(ev.RiskChanged, on_risk_changed)
    bus.subscribe(ev.StopModified, on_stop_modified)
    bus.subscribe(ev.TakeProfitModified, on_take_profit_modified)
    bus.subscribe(ev.PortfolioViewed, on_portfolio_viewed)
    bus.subscribe(ev.MorningBriefGenerated, on_morning_brief_generated)


__all__ = [
    "on_morning_brief_generated",
    "on_portfolio_viewed",
    "on_position_closed",
    "on_position_opened",
    "on_position_requested",
    "on_risk_changed",
    "on_signal_viewed",
    "on_stop_modified",
    "on_take_profit_modified",
    "register_default_handlers",
]
