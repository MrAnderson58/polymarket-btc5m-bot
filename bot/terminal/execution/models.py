"""Execution DTOs and request/result contracts for Terminal V6."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Side = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class OpenPositionRequest:
    symbol: str
    side: Side
    size: float | None = None
    entry: float | None = None
    stop: float | None = None
    take_profit: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClosePositionRequest:
    symbol: str
    side: Side | None = None
    position_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModifyStopRequest:
    symbol: str
    stop: float
    position_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModifyTakeProfitRequest:
    symbol: str
    take_profit: float
    position_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    operation: str
    message: str
    code: str = "ok"
    data: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "ClosePositionRequest",
    "ExecutionResult",
    "ModifyStopRequest",
    "ModifyTakeProfitRequest",
    "OpenPositionRequest",
    "Side",
]
