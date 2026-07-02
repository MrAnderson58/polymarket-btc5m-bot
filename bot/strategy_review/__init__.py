"""Trading AI Strategy Review v1."""

from bot.strategy_review.builder import build_strategy_review

__all__ = ["build_strategy_review"]


def __getattr__(name: str):
    if name == "build_strategy_review":
        from bot.strategy_review.builder import build_strategy_review

        return build_strategy_review
    raise AttributeError(name)
