"""UI / navigation commands for Terminal flow (V7.1.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from bot.terminal.commands.commands import TerminalCommand


@dataclass(frozen=True)
class NavigateCommand:
    """Open a Terminal screen (inline edit)."""

    screen: str
    chat_id: str
    symbol: str | None = None
    args: tuple[str, ...] = ()
    source: str = "telegram"
    edit: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToggleWatchCommand:
    chat_id: str
    symbol: str
    source: str = "telegram"
    edit: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


UiCommand = Union[NavigateCommand, ToggleWatchCommand]
AnyTerminalCommand = Union[TerminalCommand, UiCommand]


__all__ = [
    "AnyTerminalCommand",
    "NavigateCommand",
    "ToggleWatchCommand",
    "UiCommand",
]
