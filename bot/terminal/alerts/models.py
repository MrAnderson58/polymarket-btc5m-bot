"""Alert Engine DTOs (V6.2.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal
from uuid import uuid4


class AlertKind(str, Enum):
    """Supported alert families (V6.2.2 — stubs grow later)."""

    PRICE = "price"
    SIGNAL = "signal"
    AI = "ai"


AlertKindName = Literal["price", "signal", "ai"]


@dataclass(frozen=True)
class AlertCondition:
    """Single predicate on a scan / price snapshot."""

    field: str
    op: str
    value: float | str

    def describe(self) -> str:
        val = self.value
        if isinstance(val, float) and val == int(val):
            val = int(val)
        return f"{self.field}{self.op}{val}"


@dataclass(frozen=True)
class AlertRule:
    """User notification rule — evaluated against Scanner / price context."""

    rule_id: str
    user_id: str
    kind: AlertKind
    symbol: str
    conditions: tuple[AlertCondition, ...]
    enabled: bool = True
    message: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        conds = " AND ".join(c.describe() for c in self.conditions) or "(always)"
        return f"{self.symbol} {conds}"


@dataclass(frozen=True)
class AlertHit:
    """Fired notification when a rule matches."""

    rule_id: str
    user_id: str
    symbol: str
    kind: AlertKind
    reason: str
    score: float | None = None
    direction: str | None = None
    provider: str | None = None
    reasons: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


def new_rule_id() -> str:
    return uuid4().hex[:12]


__all__ = [
    "AlertCondition",
    "AlertHit",
    "AlertKind",
    "AlertKindName",
    "AlertRule",
    "new_rule_id",
]
