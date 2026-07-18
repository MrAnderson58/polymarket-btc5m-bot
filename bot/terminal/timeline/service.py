"""TimelineService — append/query instrument events (read API for UI)."""

from __future__ import annotations

from bot.terminal.timeline.models import TimelineEvent, utc_hm, utc_iso

_MAX_PER_SYMBOL = 100


class TimelineService:
    def __init__(self) -> None:
        self._events: dict[str, list[TimelineEvent]] = {}

    def record(
        self,
        symbol: str,
        kind: str,
        message: str,
        *,
        ts: str | None = None,
        **extra: object,
    ) -> TimelineEvent:
        sym = (symbol or "").upper().replace("USDT", "")
        event = TimelineEvent(
            symbol=sym,
            kind=kind,
            message=message,
            ts=ts or utc_hm(),
            extra={"at": utc_iso(), **{k: v for k, v in extra.items()}},
        )
        bucket = self._events.setdefault(sym, [])
        bucket.append(event)
        if len(bucket) > _MAX_PER_SYMBOL:
            del bucket[: len(bucket) - _MAX_PER_SYMBOL]
        return event

    def for_symbol(self, symbol: str, *, limit: int = 20) -> list[TimelineEvent]:
        sym = (symbol or "").upper().replace("USDT", "")
        rows = self._events.get(sym, [])
        return list(rows[-max(0, limit) :])

    def clear(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._events.clear()
        else:
            self._events.pop(symbol.upper().replace("USDT", ""), None)


_default: TimelineService | None = None


def get_timeline_service() -> TimelineService:
    global _default
    if _default is None:
        _default = TimelineService()
    return _default


def reset_timeline_service() -> None:
    global _default
    if _default is not None:
        _default.clear()
    _default = None


__all__ = [
    "TimelineService",
    "get_timeline_service",
    "reset_timeline_service",
]
