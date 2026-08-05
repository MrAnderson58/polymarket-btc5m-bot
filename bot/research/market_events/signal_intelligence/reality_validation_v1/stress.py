"""PART 4 — Reality stress tests (research-only)."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.reality_validation_v1.metrics import (
    basic_metrics,
    extract_pnls,
    sort_chrono,
)


DEFAULT_FEE_BPS = 10.0
UNIT = 100.0  # assumed notional per journal trade


def _apply_cost(pnls: np.ndarray, fee_bps: float, slip_bps: float = 0.0) -> np.ndarray:
    cost = UNIT * (fee_bps + slip_bps) / 10_000.0
    return pnls - cost


def reality_stress(
    trades: Sequence[dict[str, Any]],
    *,
    seed: int = 7,
) -> dict[str, Any]:
    """Hostile cost / execution / liquidity stresses — try to break the edge."""
    rows = sort_chrono(trades)
    base_list = extract_pnls(rows)
    base = np.array(base_list, dtype=float)
    n = int(base.size)
    if n == 0:
        return {"ok": False, "scenarios": [], "reason": "empty"}

    rng = np.random.default_rng(seed)
    scenarios: list[dict[str, Any]] = []

    def add(name: str, pnls: np.ndarray) -> None:
        m = basic_metrics(pnls.tolist())
        scenarios.append({"stress": name, **m})

    add("baseline", base)
    add("double_fees", _apply_cost(base, DEFAULT_FEE_BPS * 2))
    add("triple_fees", _apply_cost(base, DEFAULT_FEE_BPS * 3))

    # Random slippage 0–30 bps per trade
    slip = rng.uniform(0, 30.0, size=n)
    add("random_slippage", _apply_cost(base, DEFAULT_FEE_BPS, slip_bps=0) - UNIT * slip / 10_000.0)

    # Execution delay: shift pnl by 1 trade (miss first edge, take next)
    delayed = np.roll(base, 1)
    delayed[0] = 0.0
    add("execution_delay", delayed)

    # Missed trades: drop random 15%
    keep = rng.random(n) > 0.15
    missed = base.copy()
    missed[~keep] = 0.0
    add("missed_trades", missed)

    # Half liquidity: halve absolute pnl (worse fills)
    add("half_liquidity", base * 0.5)

    # Gap losses: inject extra -2R losses on random 5% of trades
    gaps = base.copy()
    gap_mask = rng.random(n) < 0.05
    gaps[gap_mask] = gaps[gap_mask] - 2.0 * UNIT * 0.02  # ~ -4 USD shock
    add("gap_losses", gaps)

    # Survive count: scenarios still profitable
    survive = sum(1 for s in scenarios if s.get("stress") != "baseline" and (s.get("pnl") or 0) > 0)
    n_adv = max(1, len(scenarios) - 1)
    return {
        "ok": True,
        "scenarios": scenarios,
        "survive_count": survive,
        "survive_rate": round(survive / n_adv, 4),
        "weakest": min(
            (s for s in scenarios if s.get("stress") != "baseline"),
            key=lambda s: float(s.get("pnl") or 0),
            default=None,
        ),
    }


__all__ = ["reality_stress"]
