"""PART 1 — Walk-forward validation (research-only)."""

from __future__ import annotations

from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.reality_validation_v1.metrics import (
    basic_metrics,
    extract_pnls,
    sort_chrono,
)


def walk_forward(
    trades: Sequence[dict[str, Any]],
    *,
    min_train: int = 50,
    step: int = 25,
    val_frac: float = 0.25,
) -> dict[str, Any]:
    """
    Expanding-window walk-forward over chronological trades.
    Train → validate → expand → repeat across entire history.
    """
    rows = sort_chrono(trades)
    n = len(rows)
    if n < max(min_train + 5, 20):
        return {
            "ok": False,
            "n_folds": 0,
            "folds": [],
            "train_mean_pnl": None,
            "val_mean_pnl": None,
            "gap": None,
            "reason": "insufficient_trades",
        }
    folds: list[dict[str, Any]] = []
    train_end = min_train
    while train_end < n - 5:
        val_n = max(5, int(train_end * val_frac))
        val_end = min(n, train_end + val_n)
        if val_end <= train_end:
            break
        train = rows[:train_end]
        val = rows[train_end:val_end]
        tm = basic_metrics(extract_pnls(train))
        vm = basic_metrics(extract_pnls(val))
        gap = None
        if tm.get("expectancy") is not None and vm.get("expectancy") is not None:
            gap = float(tm["expectancy"]) - float(vm["expectancy"])
        folds.append({
            "train_n": len(train),
            "val_n": len(val),
            "train_pnl": tm.get("pnl"),
            "val_pnl": vm.get("pnl"),
            "train_wr": tm.get("wr"),
            "val_wr": vm.get("wr"),
            "train_sharpe": tm.get("sharpe"),
            "val_sharpe": vm.get("sharpe"),
            "gap_expectancy": round(gap, 6) if gap is not None else None,
        })
        train_end += max(1, step)
        if train_end >= n:
            break

    if not folds:
        return {"ok": False, "n_folds": 0, "folds": [], "reason": "no_folds"}

    t_pnls = [f["train_pnl"] for f in folds if f.get("train_pnl") is not None]
    v_pnls = [f["val_pnl"] for f in folds if f.get("val_pnl") is not None]
    gaps = [f["gap_expectancy"] for f in folds if f.get("gap_expectancy") is not None]
    train_mean = sum(t_pnls) / len(t_pnls) if t_pnls else None
    val_mean = sum(v_pnls) / len(v_pnls) if v_pnls else None
    gap_mean = sum(gaps) / len(gaps) if gaps else None
    # fraction of folds where val pnl > 0
    val_pos = sum(1 for v in v_pnls if v > 0) / len(v_pnls) if v_pnls else 0.0
    return {
        "ok": True,
        "n_folds": len(folds),
        "folds": folds,
        "train_mean_pnl": round(train_mean, 6) if train_mean is not None else None,
        "val_mean_pnl": round(val_mean, 6) if val_mean is not None else None,
        "gap": round(gap_mean, 6) if gap_mean is not None else None,
        "val_positive_rate": round(val_pos, 4),
    }


__all__ = ["walk_forward"]
