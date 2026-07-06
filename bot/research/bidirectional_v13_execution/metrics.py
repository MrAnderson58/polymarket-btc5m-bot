"""Executable trade metrics for V1.3 research."""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Any

from bot.research.bidirectional_live_audit import (
    LiveTrade,
    _max_consecutive_losses,
    _max_drawdown,
    _pf,
    profit_concentration,
    rolling_windows,
)
from bot.research.bidirectional_v13_execution.config import (
    MIN_SAMPLE_EXPLORATORY,
    MIN_SAMPLE_PROMOTABLE,
    PROMOTION_GATES,
)

BOOTSTRAP_SAMPLES = 1000


@dataclass
class ExecutableTrade:
    trade: LiveTrade
    entry_price: float
    exit_price: float
    pnl_pct: float
    entry_model: str
    exit_model: str
    fill_ts: int | None = None


def bootstrap_pp_pf_gt1(pnls: list[float], n_samples: int = BOOTSTRAP_SAMPLES) -> float | None:
    if len(pnls) < MIN_SAMPLE_EXPLORATORY:
        return None
    rng = random.Random(42)
    n = len(pnls)
    hits = 0
    for _ in range(n_samples):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        if _pf(sample) > 1.0:
            hits += 1
    return round(hits / n_samples, 3)


def chronological_quarters(trades: list[ExecutableTrade]) -> list[dict[str, Any]]:
    if not trades:
        return []
    sorted_t = sorted(trades, key=lambda x: x.trade.entry_ts)
    n = len(sorted_t)
    q_size = max(1, n // 4)
    quarters = []
    for i in range(4):
        chunk = sorted_t[i * q_size: (i + 1) * q_size if i < 3 else n]
        if not chunk:
            continue
        pnls = [t.pnl_pct for t in chunk]
        quarters.append({
            "quarter": i + 1,
            "n": len(chunk),
            "pf": round(_pf(pnls), 3),
        })
    return quarters


def evaluate_executable_trades(
    trades: list[ExecutableTrade],
    *,
    signals: int,
    fills: int,
    entry_model: str = "",
    exit_model: str = "",
) -> dict[str, Any]:
    if not trades:
        return {
            "signals": signals,
            "fills": fills,
            "fill_rate": round(fills / signals, 3) if signals else 0.0,
            "closed_trades": 0,
            "sample_warning": "no_fills",
        }

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    yes_pnls = [t.pnl_pct for t in trades if t.trade.side == "YES"]
    no_pnls = [t.pnl_pct for t in trades if t.trade.side == "NO"]

    sorted_t = sorted(trades, key=lambda x: x.trade.entry_ts)
    last50 = sorted_t[-50:]
    last100 = sorted_t[-100:]

    live_for_rolling = [
        LiveTrade(
            id=t.trade.id,
            market_slug=t.trade.market_slug,
            window_start_ts=t.trade.window_start_ts,
            side=t.trade.side,
            entry_price=t.entry_price,
            entry_ts=t.trade.entry_ts,
            entry_regime=t.trade.entry_regime,
            entry_confidence=t.trade.entry_confidence,
            exit_price=t.exit_price,
            exit_reason=t.trade.exit_reason,
            pnl_pct=t.pnl_pct,
            holding_time_seconds=t.trade.holding_time_seconds,
            max_price_seen=t.trade.max_price_seen,
            btc_move_30s=t.trade.btc_move_30s,
            seconds_left=t.trade.seconds_left,
        )
        for t in sorted_t
    ]
    rolling = rolling_windows(live_for_rolling, 50)
    recent = rolling[-PROMOTION_GATES["rolling_windows"]:] if rolling else []
    rolling_pass = sum(1 for w in recent if w.get("pf", 0) > 1.0)

    conc = profit_concentration(live_for_rolling)
    max_conc = max(
        conc.get("max_side_pct", 0),
        conc.get("max_regime_pct", 0),
    )

    unique_markets = len({t.trade.market_slug for t in trades})
    n = len(trades)

    warning = None
    if n < MIN_SAMPLE_EXPLORATORY:
        warning = "exploratory_only"
    elif n < MIN_SAMPLE_PROMOTABLE:
        warning = "not_promotable"

    return {
        "entry_model": entry_model,
        "exit_model": exit_model,
        "signals": signals,
        "fills": fills,
        "fill_rate": round(fills / signals, 3) if signals else 0.0,
        "closed_trades": n,
        "unique_markets": unique_markets,
        "pf": round(_pf(pnls), 3),
        "wr": round(len(wins) / n * 100, 1),
        "avg_pnl": round(statistics.mean(pnls), 2),
        "median_pnl": round(statistics.median(pnls), 2),
        "max_dd": round(_max_drawdown(pnls), 2),
        "max_cl": _max_consecutive_losses(pnls),
        "yes_pf": round(_pf(yes_pnls), 3) if yes_pnls else None,
        "no_pf": round(_pf(no_pnls), 3) if no_pnls else None,
        "last50_pf": round(_pf([t.pnl_pct for t in last50]), 3) if last50 else None,
        "last100_pf": round(_pf([t.pnl_pct for t in last100]), 3) if last100 else None,
        "quarters": chronological_quarters(trades),
        "rolling_pf": [round(w.get("pf", 0), 3) for w in recent],
        "rolling_pass": rolling_pass,
        "bootstrap_pp_pf_gt1": bootstrap_pp_pf_gt1(pnls),
        "concentration": conc,
        "max_concentration_pct": round(max_conc, 1),
        "sample_warning": warning,
    }


def walk_forward_oos(
    trades: list[ExecutableTrade],
    train_ratio: float = 0.70,
) -> dict[str, Any]:
    if len(trades) < 20:
        return {"train_n": 0, "test_n": 0, "train_pf": 0, "oos_pf": 0}
    sorted_t = sorted(trades, key=lambda x: x.trade.entry_ts)
    split = max(1, int(len(sorted_t) * train_ratio))
    train = sorted_t[:split]
    test = sorted_t[split:]
    train_pf = _pf([t.pnl_pct for t in train])
    test_pf = _pf([t.pnl_pct for t in test])
    return {
        "train_n": len(train),
        "test_n": len(test),
        "train_pf": round(train_pf, 3),
        "oos_pf": round(test_pf, 3),
    }


def passes_promotion_gates(metrics: dict[str, Any], *, is_passive: bool = False) -> dict[str, bool]:
    gates = PROMOTION_GATES
    return {
        "min_filled_markets": metrics.get("unique_markets", 0) >= gates["min_filled_markets"],
        "executable_pf": (metrics.get("pf") or 0) >= gates["executable_pf_min"],
        "oos_pf": True,  # filled by caller with walk_forward
        "yes_pf": (metrics.get("yes_pf") or 0) >= gates["side_pf_min"] if metrics.get("yes_pf") else True,
        "no_pf": (metrics.get("no_pf") or 0) >= gates["side_pf_min"] if metrics.get("no_pf") else True,
        "bootstrap": (metrics.get("bootstrap_pp_pf_gt1") or 0) >= gates["bootstrap_pp_min"],
        "rolling": metrics.get("rolling_pass", 0) >= gates["rolling_pass_min"],
        "concentration": metrics.get("max_concentration_pct", 100) <= gates["max_concentration_pct"],
        "fill_rate_passive": (
            metrics.get("fill_rate", 0) >= gates["passive_min_fill_rate"] if is_passive else True
        ),
    }


def determine_verdict(
    best: dict[str, Any] | None,
    wf: dict[str, Any],
    gates: dict[str, bool],
) -> str:
    if best is None or best.get("closed_trades", 0) < MIN_SAMPLE_EXPLORATORY:
        return "COLLECT_MORE_DATA"
    if not gates.get("min_filled_markets"):
        return "COLLECT_MORE_DATA"
    if not gates.get("executable_pf"):
        return "NO_EXECUTABLE_EDGE"
    wf_ok = (wf.get("oos_pf") or 0) >= PROMOTION_GATES["oos_pf_min"]
    if not wf_ok:
        return "EXECUTION_EDGE_PROMISING"
    core = [
        gates.get("executable_pf"),
        gates.get("yes_pf") and gates.get("no_pf"),
        gates.get("bootstrap"),
        gates.get("rolling"),
        gates.get("concentration"),
        wf_ok,
    ]
    if all(core):
        return "READY_FOR_V13_SHADOW"
    return "EXECUTION_EDGE_PROMISING"
