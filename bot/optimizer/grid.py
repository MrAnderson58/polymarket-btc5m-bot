"""Parameter grid search (Block 2)."""

from __future__ import annotations

import itertools
from typing import Any

from bot.config import (
    ER_V2_STOP_LOSS_PCT,
    ER_V2_TIME_STOP_SEC,
    TRAILING_ACTIVATION_PROFIT,
    TRAILING_OFFSET,
)
from bot.optimizer.constants import (
    BTC_FILTER_GRID,
    ENTRY_GRID,
    STOP_GRID,
    TIME_STOP_GRID,
    TRAILING_ACTIVATION_GRID,
    TRAILING_DISTANCE_GRID,
)
from bot.optimizer.replay import StrategyParams, TradeReplay, simulate_batch


def _current_params() -> StrategyParams:
    return StrategyParams(
        max_entry=0.40,
        stop_pct=ER_V2_STOP_LOSS_PCT,
        trailing_activation=TRAILING_ACTIVATION_PROFIT,
        trailing_distance=TRAILING_OFFSET,
        time_stop_sec=ER_V2_TIME_STOP_SEC,
        btc_filter_usd=999.0,
    )


def run_grid_search(
    replays: list[TradeReplay],
    *,
    max_combos: int | None = None,
) -> dict[str, Any]:
    current = _current_params()
    current_metrics = simulate_batch(replays, current)

    best_metrics: dict[str, float | int] | None = None
    best_params: StrategyParams | None = None
    combos_tested = 0

    product = itertools.product(
        ENTRY_GRID,
        STOP_GRID,
        TRAILING_ACTIVATION_GRID,
        TRAILING_DISTANCE_GRID,
        TIME_STOP_GRID,
        BTC_FILTER_GRID,
    )
    total = (
        len(ENTRY_GRID)
        * len(STOP_GRID)
        * len(TRAILING_ACTIVATION_GRID)
        * len(TRAILING_DISTANCE_GRID)
        * len(TIME_STOP_GRID)
        * len(BTC_FILTER_GRID)
    )

    for entry, stop, tact, tdist, tstop, btc_f in product:
        combos_tested += 1
        if max_combos is not None and combos_tested > max_combos:
            break
        params = StrategyParams(
            max_entry=entry,
            stop_pct=stop,
            trailing_activation=tact,
            trailing_distance=tdist,
            time_stop_sec=tstop,
            btc_filter_usd=btc_f,
        )
        metrics = simulate_batch(replays, params)
        if metrics["trades"] < 30:
            continue
        if best_metrics is None or metrics["net_profit"] > best_metrics["net_profit"]:
            best_metrics = metrics
            best_params = params

    if best_params is None or best_metrics is None:
        best_params = current
        best_metrics = current_metrics

    if current_metrics["avg_pnl"] != 0:
        improvement = (
            (best_metrics["avg_pnl"] - current_metrics["avg_pnl"])
            / abs(current_metrics["avg_pnl"])
            * 100
        )
    else:
        improvement = best_metrics["avg_pnl"]

    return {
        "combos_tested": combos_tested,
        "combos_total": total,
        "current": {
            "entry": current.max_entry,
            "stop_pct": current.stop_pct,
            "trailing_activation": current.trailing_activation,
            "trailing_distance": current.trailing_distance,
            "time_stop_sec": current.time_stop_sec,
            "btc_filter_usd": current.btc_filter_usd,
            **current_metrics,
        },
        "optimal": {
            "entry": best_params.max_entry,
            "stop_pct": best_params.stop_pct,
            "trailing_activation": best_params.trailing_activation,
            "trailing_distance": best_params.trailing_distance,
            "time_stop_sec": best_params.time_stop_sec,
            "btc_filter_usd": best_params.btc_filter_usd,
            **best_metrics,
        },
        "expected_improvement_pct": improvement,
    }
