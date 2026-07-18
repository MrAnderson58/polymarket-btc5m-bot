"""Terminal session models (V7.1.1)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class TerminalSession:
    """Per-chat navigation state."""

    chat_id: str
    screen: str = "home"
    symbol: str | None = None
    last_decision: dict[str, Any] | None = None
    active_filter: str | None = None
    language: str = "ru"
    timezone: str = "UTC"
    history: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **kwargs: Any) -> TerminalSession:
        return replace(self, **kwargs)


__all__ = ["TerminalSession"]
