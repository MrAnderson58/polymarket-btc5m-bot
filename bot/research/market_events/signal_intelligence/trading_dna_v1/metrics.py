"""Shared metrics for Trading DNA V1."""

from __future__ import annotations

import math
from typing import Any, Sequence


def trade_metrics(pnls: Sequence[float]) -> dict[str, Any]:
    xs = [float(p) for p in pnls]
    n = len(xs)
    if n == 0:
        return {
            "n": 0,
            "wr": None,
            "ev": None,
            "pf": None,
            "total": 0.0,
            "confidence": 0.0,
            "n_nonzero": 0,
        }
    wins = [p for p in xs if p > 0]
    losses = [p for p in xs if p < 0]
    nonzero = [p for p in xs if abs(p) > 1e-12]
    gw, gl = sum(wins), abs(sum(losses))
    if gl > 1e-12:
        pf: float | None = round(gw / gl, 4)
    elif gw > 0:
        pf = None  # infinite
    else:
        pf = 0.0
    mean = sum(xs) / n
    wr = len(wins) / n
    conf = min(0.99, max(0.05, 0.5 * (1.0 - math.exp(-len(nonzero) / 80.0)) + 0.5 * abs(wr - 0.5) * 2))
    if pf is not None:
        conf = min(0.99, conf * (0.7 + 0.3 * min(3.0, pf) / 3.0))
    avg_win = round(sum(wins) / len(wins), 4) if wins else None
    avg_loss = round(sum(losses) / len(losses), 4) if losses else None
    return {
        "n": n,
        "wr": round(100.0 * wr, 2),
        "ev": round(mean, 4),
        "pf": pf,
        "pf_inf": pf is None and gw > 0,
        "gross_profit": round(gw, 4),
        "gross_loss": round(gl, 4),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "total": round(sum(xs), 4),
        "confidence": round(conf, 4),
        "n_nonzero": len(nonzero),
        "n_wins": len(wins),
        "n_losses": len(losses),
    }


def pf_sort_key(row: dict[str, Any]) -> tuple[float, float, int, float]:
    """Sort by PF, EV, n, confidence (desc). Infinite PF ranked below strong finite PF."""
    pf = row.get("pf")
    if pf is None and row.get("pf_inf"):
        pf_v = 20.0 + min(30.0, float(row.get("n_wins") or 0) / 20.0)
    else:
        pf_v = float(pf or 0.0)
    return (
        pf_v,
        float(row.get("ev") or 0.0),
        int(row.get("n_nonzero") or row.get("n") or 0),
        float(row.get("confidence") or 0.0),
    )


__all__ = ["pf_sort_key", "trade_metrics"]
