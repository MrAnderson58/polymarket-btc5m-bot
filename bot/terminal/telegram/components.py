"""Reusable Terminal render components — screens compose these only."""

from __future__ import annotations

from bot.terminal.telegram.formatters import progress_bar, status_badge


class StatusRenderer:
    @staticmethod
    def render(state: str | None) -> str:
        return status_badge(state)


class ProgressBarRenderer:
    @staticmethod
    def render(pct: float | None, *, width: int = 10, label: str | None = None) -> str:
        bar = progress_bar(pct, width=width)
        if label:
            return f"{label}\n{bar}"
        return bar


class SectionRenderer:
    @staticmethod
    def render(title: str, *lines: str) -> str:
        body = [title, ""]
        body.extend(line for line in lines if line is not None)
        return "\n".join(body).rstrip()


class CardRenderer:
    @staticmethod
    def render(title: str, sections: list[str], *, footer: str | None = None) -> str:
        parts: list[str] = [title, ""]
        for i, section in enumerate(sections):
            if not section:
                continue
            parts.append(section.rstrip())
            if i < len(sections) - 1:
                parts.append("")
        if footer:
            parts.extend(["", footer])
        return "\n".join(parts).rstrip()


__all__ = [
    "CardRenderer",
    "ProgressBarRenderer",
    "SectionRenderer",
    "StatusRenderer",
]
