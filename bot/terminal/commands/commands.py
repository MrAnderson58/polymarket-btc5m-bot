"""Terminal command objects (intent) — not Telegram slash strings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

Side = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class OpenPositionCommand:
    symbol: str
    side: Side
    size: float | None = None
    entry: float | None = None
    stop: float | None = None
    take_profit: float | None = None
    source: str = "telegram"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClosePositionCommand:
    symbol: str
    side: Side | None = None
    position_id: str | None = None
    source: str = "telegram"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModifyRiskCommand:
    symbol: str | None = None
    source: str = "telegram"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModifyStopCommand:
    symbol: str
    stop: float
    position_id: str | None = None
    source: str = "telegram"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModifyTakeProfitCommand:
    symbol: str
    take_profit: float
    position_id: str | None = None
    source: str = "telegram"
    extra: dict[str, Any] = field(default_factory=dict)


TerminalCommand = Union[
    OpenPositionCommand,
    ClosePositionCommand,
    ModifyRiskCommand,
    ModifyStopCommand,
    ModifyTakeProfitCommand,
]


__all__ = [
    "ClosePositionCommand",
    "ModifyRiskCommand",
    "ModifyStopCommand",
    "ModifyTakeProfitCommand",
    "OpenPositionCommand",
    "TerminalCommand",
]
