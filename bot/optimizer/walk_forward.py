"""Walk-forward validation (Block 7)."""

from __future__ import annotations

from typing import Any

from bot.optimizer.replay import StrategyParams, TradeReplay, simulate_batch


def run_walk_forward(
    replays: list[TradeReplay],
    optimal: StrategyParams,
    *,
    train_sizes: tuple[int, ...] = (300, 400, 500),
    test_size: int = 100,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    n = len(replays)
    for train_n in train_sizes:
        if n < train_n + test_size:
            continue
        train = replays[:train_n]
        test = replays[train_n : train_n + test_size]
        train_m = simulate_batch(train, optimal)
        test_m = simulate_batch(test, optimal)
        results.append(
            {
                "train_size": train_n,
                "test_size": test_size,
                "train_avg_pnl": train_m["avg_pnl"],
                "test_avg_pnl": test_m["avg_pnl"],
                "train_pf": train_m["profit_factor"],
                "test_pf": test_m["profit_factor"],
                "generalizes": test_m["avg_pnl"] > 0 and test_m["profit_factor"] >= 1.0,
            }
        )
    return results
