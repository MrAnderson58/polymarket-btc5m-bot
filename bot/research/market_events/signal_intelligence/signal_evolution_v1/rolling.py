"""Rolling edge metrics for Signal Evolution V1."""

from __future__ import annotations

from typing import Any

import numpy as np

ROLLING_WINDOWS = (50, 100, 250, 500, 1000)


def _sharpe(pnls: np.ndarray) -> float | None:
    if pnls.size < 2:
        return None
    std = float(pnls.std(ddof=1))
    if std <= 1e-12:
        return None
    return round(float(pnls.mean() / std) * np.sqrt(min(252, pnls.size)), 4)


def _max_dd(pnls: np.ndarray) -> float:
    if pnls.size == 0:
        return 0.0
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    return round(float(dd.min()), 4)


def _pf(pnls: np.ndarray) -> float | None:
    gains = float(pnls[pnls > 0].sum())
    losses = float(-pnls[pnls < 0].sum())
    if losses <= 1e-12:
        return None if gains <= 0 else None
    return round(gains / losses, 4)


def window_metrics(pnls: list[float] | np.ndarray) -> dict[str, Any]:
    arr = np.asarray(list(pnls), dtype=float)
    n = int(arr.size)
    if n == 0:
        return {
            "n": 0, "wr": None, "pf": None, "ev": None,
            "sharpe": None, "max_dd": None,
        }
    wins = int((arr > 0).sum())
    return {
        "n": n,
        "wr": round(wins / n, 4),
        "pf": _pf(arr),
        "ev": round(float(arr.mean()), 6),
        "sharpe": _sharpe(arr),
        "max_dd": _max_dd(arr),
    }


def rolling_edge(pnls_newest_last: list[float]) -> dict[str, Any]:
    """pnls chronological (oldest→newest). Returns last-N window metrics."""
    out: dict[str, Any] = {}
    for w in ROLLING_WINDOWS:
        chunk = pnls_newest_last[-w:] if len(pnls_newest_last) else []
        out[f"last_{w}"] = window_metrics(chunk)
    out["all"] = window_metrics(pnls_newest_last)
    return out


__all__ = ["ROLLING_WINDOWS", "rolling_edge", "window_metrics"]
