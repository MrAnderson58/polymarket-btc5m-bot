"""Terminal Telegram package — interactive navigation UI."""

from bot.terminal.telegram.terminal_router import (
    dispatch_terminal_command,
    handle_terminal_callback,
    is_terminal_command,
)

__all__ = [
    "dispatch_terminal_command",
    "handle_terminal_callback",
    "is_terminal_command",
]
