"""Shared display formatters for Terminal UI."""

from __future__ import annotations


def format_money(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "—"
    if signed:
        sign = "+" if value > 0 else ("" if value == 0 else "")
        # Match acceptance: $102,340 and +3.24% style money with $ and commas
        if value < 0:
            return f"-${abs(value):,.2f}"
        if value > 0 and signed:
            return f"${value:,.2f}"
        return f"${value:,.2f}"
    return f"${abs(value):,.2f}"


def format_pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "—"
    if signed:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def progress_bar(pct: float | None, *, width: int = 10) -> str:
    """Universal bar: ████████░░ 80%"""
    if pct is None:
        return "░" * width + " —"
    p = max(0.0, min(100.0, float(pct)))
    filled = int(round((p / 100.0) * width))
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {p:.0f}%"


def status_badge(state: str | None) -> str:
    s = (state or "unknown").strip().lower()
    if s in {"online", "ok", "pass", "healthy", "up", "true", "1"}:
        return "🟢 ONLINE"
    if s in {"warning", "degraded", "warn", "stale"}:
        return "🟡 WARNING"
    if s in {"offline", "down", "fail", "error", "false", "0"}:
        return "🔴 OFFLINE"
    return "⚪ UNKNOWN"


__all__ = [
    "format_duration",
    "format_money",
    "format_pct",
    "progress_bar",
    "status_badge",
]
