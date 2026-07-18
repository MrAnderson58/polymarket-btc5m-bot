"""Minimal synchronous in-process EventBus."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, DefaultDict, TypeVar

T = TypeVar("T")
Handler = Callable[[Any], None]


class EventBus:
    """Sync publish/subscribe — no Redis/Kafka."""

    def __init__(self) -> None:
        self._handlers: DefaultDict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type[T], handler: Callable[[T], None]) -> None:
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)  # type: ignore[arg-type]

    def unsubscribe(self, event_type: type[T], handler: Callable[[T], None]) -> None:
        handlers = self._handlers.get(event_type) or []
        if handler in handlers:
            handlers.remove(handler)  # type: ignore[arg-type]

    def publish(self, event: Any) -> None:
        for handler in list(self._handlers.get(type(event), [])):
            handler(event)


_default_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
        from bot.terminal.events.handlers import register_default_handlers

        register_default_handlers(_default_bus)
    return _default_bus


def reset_event_bus() -> EventBus:
    """Test helper — fresh bus without default handlers unless re-registered."""
    global _default_bus
    _default_bus = EventBus()
    return _default_bus


__all__ = ["EventBus", "get_event_bus", "reset_event_bus"]
