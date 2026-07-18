"""Terminal execution package — Paper adapter first; live venues later."""

from bot.terminal.execution.execution_service import (
    ExecutionAdapter,
    ExecutionService,
    get_execution_service,
)
from bot.terminal.execution.models import (
    ClosePositionRequest,
    ExecutionResult,
    ModifyStopRequest,
    ModifyTakeProfitRequest,
    OpenPositionRequest,
)
from bot.terminal.execution.paper_adapter import PaperAdapter

__all__ = [
    "ClosePositionRequest",
    "ExecutionAdapter",
    "ExecutionResult",
    "ExecutionService",
    "ModifyStopRequest",
    "ModifyTakeProfitRequest",
    "OpenPositionRequest",
    "PaperAdapter",
    "get_execution_service",
]
