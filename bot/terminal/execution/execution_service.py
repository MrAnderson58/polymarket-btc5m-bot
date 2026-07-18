"""ExecutionService — adapter-agnostic execution contract (Paper / future Live)."""

from __future__ import annotations

from typing import Protocol

from bot.terminal.execution.models import (
    ClosePositionRequest,
    ExecutionResult,
    ModifyStopRequest,
    ModifyTakeProfitRequest,
    OpenPositionRequest,
)
from bot.terminal.models.dto import AccountCard, PositionCard


class ExecutionAdapter(Protocol):
    """Backend adapter (PaperAdapter, future BybitAdapter, …)."""

    def get_balance(self) -> AccountCard:
        ...

    def get_positions(self) -> list[PositionCard]:
        ...

    def open_position(self, request: OpenPositionRequest) -> ExecutionResult:
        ...

    def close_position(self, request: ClosePositionRequest) -> ExecutionResult:
        ...

    def modify_stop(self, request: ModifyStopRequest) -> ExecutionResult:
        ...

    def modify_take_profit(self, request: ModifyTakeProfitRequest) -> ExecutionResult:
        ...

    def cancel_orders(self, *, symbol: str | None = None) -> ExecutionResult:
        ...


class ExecutionService:
    """Thin façade: Terminal talks only to this service, never to venues."""

    def __init__(self, adapter: ExecutionAdapter) -> None:
        self._adapter = adapter

    def get_balance(self) -> AccountCard:
        return self._adapter.get_balance()

    def get_positions(self) -> list[PositionCard]:
        return self._adapter.get_positions()

    def open_position(self, request: OpenPositionRequest) -> ExecutionResult:
        if request.side not in ("LONG", "SHORT"):
            return ExecutionResult(
                ok=False,
                operation="open_position",
                code="invalid_side",
                message="Only LONG or SHORT allowed",
            )
        return self._adapter.open_position(request)

    def close_position(self, request: ClosePositionRequest) -> ExecutionResult:
        return self._adapter.close_position(request)

    def modify_stop(self, request: ModifyStopRequest) -> ExecutionResult:
        return self._adapter.modify_stop(request)

    def modify_take_profit(self, request: ModifyTakeProfitRequest) -> ExecutionResult:
        return self._adapter.modify_take_profit(request)

    def cancel_orders(self, *, symbol: str | None = None) -> ExecutionResult:
        return self._adapter.cancel_orders(symbol=symbol)


def get_execution_service(*, live: bool = False) -> ExecutionService:
    """Factory — Paper only for V6.0.2. Live adapters come later."""
    if live:
        raise RuntimeError("Live execution adapter is not enabled (V6.0.2 paper-only)")
    from bot.terminal.execution.paper_adapter import PaperAdapter

    return ExecutionService(PaperAdapter())


__all__ = [
    "ExecutionAdapter",
    "ExecutionService",
    "get_execution_service",
]
