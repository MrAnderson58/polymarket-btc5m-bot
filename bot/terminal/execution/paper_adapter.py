"""PaperAdapter — wraps existing paper / terminal read services.

Does not invent trading logic, SQL, or tables.
Mutating Terminal ops are gated until an existing public paper API is wired.
"""

from __future__ import annotations

from bot.terminal.execution.models import (
    ClosePositionRequest,
    ExecutionResult,
    ModifyStopRequest,
    ModifyTakeProfitRequest,
    OpenPositionRequest,
)
from bot.terminal.models.dto import AccountCard, PositionCard
from bot.terminal.services.account_service import get_account_service
from bot.terminal.services.positions_service import get_position_service

_GATED = (
    "Paper adapter available. "
    "Terminal-initiated mutate is not enabled yet "
    "(no new SQL / trading logic). "
    "Existing paper worker / S4.2 engine unchanged."
)


class PaperAdapter:
    """Read path uses existing Terminal services over S4.2 / config."""

    name = "paper"

    def get_balance(self) -> AccountCard:
        return get_account_service().get_balance()

    def get_positions(self) -> list[PositionCard]:
        return get_position_service().get_open()

    def open_position(self, request: OpenPositionRequest) -> ExecutionResult:
        _ = request
        return ExecutionResult(
            ok=False,
            operation="open_position",
            code="not_enabled",
            message=_GATED,
            data={"adapter": self.name, "allowed_sides": ["LONG", "SHORT"]},
        )

    def close_position(self, request: ClosePositionRequest) -> ExecutionResult:
        _ = request
        return ExecutionResult(
            ok=False,
            operation="close_position",
            code="not_enabled",
            message=_GATED,
            data={"adapter": self.name, "allowed_ops": ["CLOSE"]},
        )

    def modify_stop(self, request: ModifyStopRequest) -> ExecutionResult:
        _ = request
        return ExecutionResult(
            ok=False,
            operation="modify_stop",
            code="not_enabled",
            message=_GATED,
            data={"adapter": self.name},
        )

    def modify_take_profit(self, request: ModifyTakeProfitRequest) -> ExecutionResult:
        _ = request
        return ExecutionResult(
            ok=False,
            operation="modify_take_profit",
            code="not_enabled",
            message=_GATED,
            data={"adapter": self.name},
        )

    def cancel_orders(self, *, symbol: str | None = None) -> ExecutionResult:
        _ = symbol
        return ExecutionResult(
            ok=False,
            operation="cancel_orders",
            code="not_enabled",
            message=_GATED,
            data={"adapter": self.name},
        )


__all__ = ["PaperAdapter"]
