"""Terminal Telegram router — thin parse → CommandDispatcher only (V7.1.0).

No direct service calls. Callbacks always editMessageText (edit=True).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.terminal.commands.dispatcher import get_command_dispatcher
from bot.terminal.commands.ui_commands import NavigateCommand, ToggleWatchCommand
from bot.terminal.telegram import keyboards

TERMINAL_NAV_COMMANDS = frozenset({
    "/start",
    "/home",
    "/markets",
    "/signals",
    "/positions",
    "/portfolio",
    "/account",
    "/settings",
    "/watch",
    "/alert",
    "/alerts",
    "/decision",
    "/brief",
    "/research",
    "/review",
    "/why",
    "/timeline",
})

TERMINAL_EXEC_STUB_COMMANDS = frozenset({
    "/open",
    "/close",
    "/risk",
    "/sl",
    "/tp",
})

SUPPORTED_TERMINAL_COMMANDS = TERMINAL_NAV_COMMANDS | TERMINAL_EXEC_STUB_COMMANDS

DEFAULT_USER_ID = "default"


@dataclass(frozen=True)
class TerminalReply:
    text: str
    reply_markup: dict[str, Any] | None = None
    ok: bool = True
    edit: bool = False


def _parse(text: str) -> tuple[str, list[str]]:
    parts = (text or "").strip().split()
    if not parts:
        return "", []
    cmd = parts[0].lower().split("@", 1)[0]
    return cmd, parts[1:]


def _uid(user_id: str | int | None) -> str:
    if user_id is None or str(user_id).strip() == "":
        return DEFAULT_USER_ID
    return str(user_id)


def is_terminal_command(text: str | None) -> bool:
    if not text:
        return False
    cmd, _ = _parse(text)
    return cmd in SUPPORTED_TERMINAL_COMMANDS


def _from_ui(result: Any, *, force_edit: bool | None = None) -> TerminalReply:
    edit = result.edit if force_edit is None else force_edit
    return TerminalReply(
        text=result.text,
        reply_markup=result.reply_markup,
        ok=result.ok,
        edit=edit,
    )


def dispatch_terminal_command(
    text: str,
    *,
    from_callback: bool = False,
    user_id: str | int | None = None,
) -> TerminalReply | None:
    """Slash → NavigateCommand / stubs via CommandDispatcher."""
    cmd, args = _parse(text)
    if cmd not in SUPPORTED_TERMINAL_COMMANDS:
        return None

    uid = _uid(user_id)
    dispatcher = get_command_dispatcher()

    if cmd in TERMINAL_EXEC_STUB_COMMANDS:
        return _from_ui(dispatcher.dispatch_execution_stub(edit=from_callback), force_edit=from_callback)

    # Map slash to screen + optional symbol
    screen = cmd.lstrip("/")
    symbol: str | None = None
    extra_args: tuple[str, ...] = tuple(args)

    if screen in {"decision", "why", "research", "review", "timeline", "watch"}:
        if args:
            if screen == "watch" and args[0].lower() in {"add", "remove", "rm", "del", "delete"}:
                action = args[0].lower()
                if action == "add" and len(args) >= 2:
                    return _from_ui(
                        dispatcher.dispatch_ui(
                            NavigateCommand(
                                screen="watch_add",
                                chat_id=uid,
                                symbol=args[1],
                                edit=from_callback,
                            )
                        ),
                        force_edit=from_callback,
                    )
                if action in {"remove", "rm", "del", "delete"} and len(args) >= 2:
                    return _from_ui(
                        dispatcher.dispatch_ui(
                            NavigateCommand(
                                screen="watch_remove",
                                chat_id=uid,
                                symbol=args[1],
                                edit=from_callback,
                            )
                        ),
                        force_edit=from_callback,
                    )
            else:
                symbol = args[0]
                extra_args = tuple(args[1:])

    if screen == "review":
        screen = "research"
    if screen in {"alert", "alerts"}:
        screen = "alerts"

    result = dispatcher.dispatch_ui(
        NavigateCommand(
            screen=screen,
            chat_id=uid,
            symbol=symbol,
            args=extra_args,
            edit=from_callback,
        )
    )
    if screen == "start" and not from_callback:
        return _from_ui(result, force_edit=False)
    return _from_ui(result, force_edit=from_callback)


def handle_terminal_callback(
    callback_data: str,
    *,
    user_id: str | int | None = None,
) -> TerminalReply | None:
    """Inline callback → CommandDispatcher (always edit)."""
    if not keyboards.is_terminal_callback(callback_data):
        return None

    uid = _uid(user_id)
    screen, symbol = keyboards.parse_callback_intent(callback_data)
    dispatcher = get_command_dispatcher()

    if screen == "fav" and symbol:
        result = dispatcher.dispatch_ui(
            ToggleWatchCommand(chat_id=uid, symbol=symbol, edit=True)
        )
        return _from_ui(result, force_edit=True)

    result = dispatcher.dispatch_ui(
        NavigateCommand(
            screen=screen,
            chat_id=uid,
            symbol=symbol,
            edit=True,
        )
    )
    return _from_ui(result, force_edit=True)


__all__ = [
    "SUPPORTED_TERMINAL_COMMANDS",
    "TERMINAL_EXEC_STUB_COMMANDS",
    "TERMINAL_NAV_COMMANDS",
    "TerminalReply",
    "dispatch_terminal_command",
    "handle_terminal_callback",
    "is_terminal_command",
]
