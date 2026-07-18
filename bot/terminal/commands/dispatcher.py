"""Command dispatcher — Telegram → Command → Execution / ScreenController."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.terminal.commands.commands import (
    ClosePositionCommand,
    ModifyRiskCommand,
    ModifyStopCommand,
    ModifyTakeProfitCommand,
    OpenPositionCommand,
    TerminalCommand,
)
from bot.terminal.commands.screen_controller import get_screen_controller
from bot.terminal.commands.ui_commands import NavigateCommand, ToggleWatchCommand, UiCommand
from bot.terminal.events.event_bus import EventBus, get_event_bus
from bot.terminal.events.events import (
    PositionClosed,
    PositionOpened,
    PositionRequested,
    RiskChanged,
    StopModified,
    TakeProfitModified,
)
from bot.terminal.execution.execution_service import ExecutionService, get_execution_service
from bot.terminal.execution.models import (
    ClosePositionRequest,
    ExecutionResult,
    ModifyStopRequest,
    ModifyTakeProfitRequest,
    OpenPositionRequest,
)


@dataclass(frozen=True)
class UiDispatchResult:
    """UI navigation result — always edit-capable for inline flow."""

    ok: bool
    text: str
    reply_markup: dict[str, Any] | None = None
    edit: bool = True
    code: str = "ok"
    message: str = ""


class CommandDispatcher:
    def __init__(
        self,
        *,
        execution: ExecutionService | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._execution = execution or get_execution_service(live=False)
        self._bus = bus or get_event_bus()
        self._screens = get_screen_controller()

    def dispatch(self, command: TerminalCommand) -> ExecutionResult:
        if isinstance(command, OpenPositionCommand):
            return self._open(command)
        if isinstance(command, ClosePositionCommand):
            return self._close(command)
        if isinstance(command, ModifyRiskCommand):
            return self._risk(command)
        if isinstance(command, ModifyStopCommand):
            return self._stop(command)
        if isinstance(command, ModifyTakeProfitCommand):
            return self._tp(command)
        return ExecutionResult(
            ok=False,
            operation="dispatch",
            code="unknown_command",
            message=f"Unsupported command: {type(command).__name__}",
        )

    def dispatch_ui(self, command: UiCommand) -> UiDispatchResult:
        """All Terminal callbacks / nav slash commands enter here."""
        if isinstance(command, ToggleWatchCommand):
            payload = self._screens.toggle_watch(
                chat_id=command.chat_id,
                symbol=command.symbol,
                edit=command.edit,
            )
            return UiDispatchResult(
                ok=bool(payload.get("ok", True)),
                text=str(payload.get("text") or ""),
                reply_markup=payload.get("reply_markup"),
                edit=bool(payload.get("edit", True)),
                message="toggle_watch",
            )
        if isinstance(command, NavigateCommand):
            payload = self._screens.navigate(
                screen=command.screen,
                chat_id=command.chat_id,
                symbol=command.symbol,
                args=command.args,
                edit=command.edit,
            )
            return UiDispatchResult(
                ok=bool(payload.get("ok", True)),
                text=str(payload.get("text") or ""),
                reply_markup=payload.get("reply_markup"),
                edit=bool(payload.get("edit", command.edit)),
                message=command.screen,
            )
        return UiDispatchResult(
            ok=False,
            text="Unknown UI command",
            edit=True,
            code="unknown_ui",
        )

    def dispatch_execution_stub(self, *, edit: bool = False) -> UiDispatchResult:
        payload = self._screens.execution_stub(edit=edit)
        return UiDispatchResult(
            ok=True,
            text=str(payload["text"]),
            reply_markup=payload.get("reply_markup"),
            edit=edit,
            message="execution_stub",
        )

    def _open(self, command: OpenPositionCommand) -> ExecutionResult:
        self._bus.publish(
            PositionRequested(
                symbol=command.symbol,
                side=command.side,
                source=command.source,
                extra=dict(command.extra),
            )
        )
        result = self._execution.open_position(
            OpenPositionRequest(
                symbol=command.symbol,
                side=command.side,
                size=command.size,
                entry=command.entry,
                stop=command.stop,
                take_profit=command.take_profit,
                extra=dict(command.extra),
            )
        )
        if result.ok:
            self._bus.publish(
                PositionOpened(
                    symbol=command.symbol,
                    side=command.side,
                    source=command.source,
                    extra={"result": result.message, **dict(command.extra)},
                )
            )
        return result

    def _close(self, command: ClosePositionCommand) -> ExecutionResult:
        result = self._execution.close_position(
            ClosePositionRequest(
                symbol=command.symbol,
                side=command.side,
                position_id=command.position_id,
                extra=dict(command.extra),
            )
        )
        if result.ok:
            self._bus.publish(
                PositionClosed(
                    symbol=command.symbol,
                    side=command.side,
                    source=command.source,
                    extra={"result": result.message, **dict(command.extra)},
                )
            )
        return result

    def _risk(self, command: ModifyRiskCommand) -> ExecutionResult:
        self._bus.publish(
            RiskChanged(
                symbol=command.symbol,
                source=command.source,
                extra=dict(command.extra),
            )
        )
        return ExecutionResult(
            ok=False,
            operation="modify_risk",
            code="not_enabled",
            message="Risk modify via Terminal not enabled yet",
            data={"adapter": "paper"},
        )

    def _stop(self, command: ModifyStopCommand) -> ExecutionResult:
        result = self._execution.modify_stop(
            ModifyStopRequest(
                symbol=command.symbol,
                stop=command.stop,
                position_id=command.position_id,
                extra=dict(command.extra),
            )
        )
        if result.ok:
            self._bus.publish(
                StopModified(
                    symbol=command.symbol,
                    stop=command.stop,
                    source=command.source,
                    extra=dict(command.extra),
                )
            )
        return result

    def _tp(self, command: ModifyTakeProfitCommand) -> ExecutionResult:
        result = self._execution.modify_take_profit(
            ModifyTakeProfitRequest(
                symbol=command.symbol,
                take_profit=command.take_profit,
                position_id=command.position_id,
                extra=dict(command.extra),
            )
        )
        if result.ok:
            self._bus.publish(
                TakeProfitModified(
                    symbol=command.symbol,
                    take_profit=command.take_profit,
                    source=command.source,
                    extra=dict(command.extra),
                )
            )
        return result


_dispatcher: CommandDispatcher | None = None


def get_command_dispatcher() -> CommandDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = CommandDispatcher()
    return _dispatcher


def reset_command_dispatcher() -> None:
    global _dispatcher
    _dispatcher = None


__all__ = [
    "CommandDispatcher",
    "UiDispatchResult",
    "get_command_dispatcher",
    "reset_command_dispatcher",
]
