"""Trading Evolution v2 — auto-review + shadow experiments."""

from bot.evolution.state import EvolutionStatus

__all__ = [
    "EvolutionStatus",
    "build_evolution",
    "decide_evolution",
    "find_best_candidate",
    "render_evolution_block",
    "render_evolution_section",
]


def __getattr__(name: str):
    if name == "build_evolution":
        from bot.evolution.builder import build_evolution

        return build_evolution
    if name == "decide_evolution":
        from bot.evolution.decision import decide_evolution

        return decide_evolution
    if name == "find_best_candidate":
        from bot.evolution.auto_review import find_best_candidate

        return find_best_candidate
    if name == "render_evolution_block":
        from bot.evolution.render import render_evolution_block

        return render_evolution_block
    if name == "render_evolution_section":
        from bot.evolution.render import render_evolution_section

        return render_evolution_section
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
