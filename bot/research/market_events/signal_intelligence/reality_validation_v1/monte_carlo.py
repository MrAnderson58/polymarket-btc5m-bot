"""PART 3 — Adversarial Monte Carlo (research-only)."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.reality_validation_v1.metrics import (
    basic_metrics,
    extract_pnls,
    sort_chrono,
)


def _max_dd_from_pnls(pnls: np.ndarray) -> float:
    if pnls.size == 0:
        return 0.0
    eq = np.cumsum(pnls)
    peaks = np.maximum.accumulate(eq)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(np.abs(peaks) > 1e-12, (eq - peaks) / np.abs(peaks), eq - peaks)
    return float(np.min(dd))


def monte_carlo_reality(
    trades: Sequence[dict[str, Any]],
    *,
    n_sims: int = 10_000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    10k adversarial shuffles:
    - shuffle trade order
    - shuffle symbols (permute labels on pnls)
    - shuffle time (same as order)
    - shuffle winners (permute win/loss signs partially)
    - bootstrap resample
    """
    rows = sort_chrono(trades)
    pnls = np.array(extract_pnls(rows), dtype=float)
    n = int(pnls.size)
    if n < 5:
        return {"ok": False, "n_sims": 0, "reason": "insufficient_trades"}

    base = basic_metrics(pnls.tolist())
    rng = np.random.default_rng(seed)
    sims = int(n_sims)

    # Vectorized bootstrap + shuffle order
    idx = rng.integers(0, n, size=(sims, n))
    boot = pnls[idx]
    # shuffle each row in-place via argsort of random keys
    keys = rng.random(size=(sims, n))
    order = np.argsort(keys, axis=1)
    shuffled = np.take_along_axis(boot, order, axis=1)

    # symbol shuffle: permute pnl assignment (same distribution, different path)
    sym_idx = np.stack([rng.permutation(n) for _ in range(min(sims, 2000))])
    # expand if needed
    if sym_idx.shape[0] < sims:
        rep = int(np.ceil(sims / sym_idx.shape[0]))
        sym_idx = np.tile(sym_idx, (rep, 1))[:sims]
    symbol_shuffled = pnls[sym_idx]

    # winner shuffle: flip random 20% of signs
    flip_mask = rng.random(size=(sims, n)) < 0.20
    winners = pnls * np.where(flip_mask, -1.0, 1.0)

    finals_order = shuffled.sum(axis=1)
    finals_boot = boot.sum(axis=1)
    finals_sym = symbol_shuffled.sum(axis=1)
    finals_win = winners.sum(axis=1)

    # mix: half bootstrap-shuffle, half winner-flip
    half = sims // 2
    mixed = np.concatenate([finals_order[:half], finals_win[half:]])

    def _ci(arr: np.ndarray) -> dict[str, float]:
        return {
            "mean": round(float(np.mean(arr)), 6),
            "median": round(float(np.median(arr)), 6),
            "ci_5": round(float(np.percentile(arr, 5)), 6),
            "ci_95": round(float(np.percentile(arr, 95)), 6),
            "p_profit": round(float(np.mean(arr > 0)), 4),
            "p_loss": round(float(np.mean(arr < 0)), 4),
        }

    # MaxDD distribution on bootstrap-shuffled paths (sample subset for speed)
    sample_n = min(sims, 2000)
    dds = np.array([_max_dd_from_pnls(shuffled[i]) for i in range(sample_n)])

    return {
        "ok": True,
        "n_sims": sims,
        "n_trades": n,
        "base": base,
        "shuffle_order": _ci(finals_order),
        "bootstrap": _ci(finals_boot),
        "shuffle_symbols": _ci(finals_sym),
        "shuffle_winners": _ci(finals_win),
        "shuffle_time": _ci(finals_order),  # chronological destroy = order shuffle
        "mixed": _ci(mixed),
        "max_dd_mean": round(float(np.mean(dds)), 6),
        "max_dd_ci_5": round(float(np.percentile(dds, 5)), 6),
        "fragile": bool(float(np.mean(finals_order > 0)) < 0.55),
    }


__all__ = ["monte_carlo_reality"]
