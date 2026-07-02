"""Unified AI Trading Analytics Report."""

__all__ = ["build_report", "save_report"]


def __getattr__(name: str):
    if name in __all__:
        from bot.report.builder import build_report, save_report

        return {"build_report": build_report, "save_report": save_report}[name]
    raise AttributeError(name)
