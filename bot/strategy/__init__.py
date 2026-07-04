"""Strategy package — legacy late-window + bidirectional momentum.

Re-exports all public symbols from the legacy strategy module so that
existing imports like `from bot.strategy import SignalSide` keep working.
"""

from bot.strategy._legacy import SignalSide, StrategySignal, evaluate  # noqa: F401

__all__ = ["SignalSide", "StrategySignal", "evaluate"]
