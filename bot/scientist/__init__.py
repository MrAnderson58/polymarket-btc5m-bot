"""Trading AI Scientist v1 — observe-only hypothesis research."""

from bot.scientist.builder import build_scientist_section
from bot.scientist.scheduler import run_scientist_cycle

__all__ = ["build_scientist_section", "run_scientist_cycle"]


def __getattr__(name: str):
    if name == "build_scientist_section":
        from bot.scientist.builder import build_scientist_section

        return build_scientist_section
    if name == "run_scientist_cycle":
        from bot.scientist.scheduler import run_scientist_cycle

        return run_scientist_cycle
    raise AttributeError(name)
