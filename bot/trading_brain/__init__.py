"""Trading Brain v1 — SQLite ↔ AI Agent intelligence layer."""

from bot.trading_brain.learning import load_trade_context, run_brain_learning

__all__ = ["load_trade_context", "run_brain_learning"]


def __getattr__(name: str):
    if name == "run_brain_learning":
        from bot.trading_brain.learning import run_brain_learning

        return run_brain_learning
    if name == "load_trade_context":
        from bot.trading_brain.learning import load_trade_context

        return load_trade_context
    raise AttributeError(name)
