"""SessionManager — per-chat Terminal state (V7.1.1)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from bot.terminal.session.models import TerminalSession

_MAX_HISTORY = 32


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}

    def get_session(self, chat_id: str | int) -> TerminalSession:
        key = str(chat_id)
        if key not in self._sessions:
            self._sessions[key] = TerminalSession(chat_id=key)
        return self._sessions[key]

    def update_session(self, chat_id: str | int, **kwargs: Any) -> TerminalSession:
        current = self.get_session(chat_id)
        data = dict(kwargs)
        if "screen" in data and data["screen"]:
            hist = list(current.history)
            if not hist or hist[-1] != current.screen:
                hist.append(current.screen)
            hist = hist[-_MAX_HISTORY:]
            data["history"] = tuple(hist)
        if "extra" in data and isinstance(data["extra"], dict):
            merged = dict(current.extra)
            merged.update(data["extra"])
            data["extra"] = merged
        if "last_decision" in data and data["last_decision"] is not None:
            data["last_decision"] = deepcopy(data["last_decision"])
        updated = current.with_updates(**data)
        self._sessions[str(chat_id)] = updated
        return updated

    def clear_session(self, chat_id: str | int) -> None:
        self._sessions.pop(str(chat_id), None)

    def count(self) -> int:
        return len(self._sessions)

    def clear_all(self) -> None:
        self._sessions.clear()


_default: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _default
    if _default is None:
        _default = SessionManager()
    return _default


def reset_session_manager() -> None:
    global _default
    if _default is not None:
        _default.clear_all()
    _default = None


__all__ = [
    "SessionManager",
    "get_session_manager",
    "reset_session_manager",
]
