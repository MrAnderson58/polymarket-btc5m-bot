"""PART 2 — Out-of-sample time split (research-only)."""

from __future__ import annotations

from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.reality_validation_v1.metrics import (
    basic_metrics,
    extract_pnls,
    sort_chrono,
)


def out_of_sample(
    trades: Sequence[dict[str, Any]],
    *,
    train_frac: float = 0.7,
) -> dict[str, Any]:
    """
    Strict chronological split: Old (train) → New (test).
    No random split.
    """
    rows = sort_chrono(trades)
    n = len(rows)
    if n < 20:
        return {"ok": False, "reason": "insufficient_trades", "n": n}
    cut = max(10, min(n - 5, int(n * float(train_frac))))
    old = rows[:cut]
    new = rows[cut:]
    om = basic_metrics(extract_pnls(old))
    nm = basic_metrics(extract_pnls(new))
    gap_exp = None
    if om.get("expectancy") is not None and nm.get("expectancy") is not None:
        gap_exp = float(om["expectancy"]) - float(nm["expectancy"])
    gap_sharpe = None
    if om.get("sharpe") is not None and nm.get("sharpe") is not None:
        gap_sharpe = float(om["sharpe"]) - float(nm["sharpe"])
    degradation = False
    if nm.get("pnl") is not None and om.get("pnl") is not None:
        # new period loses while old was profitable
        if om["pnl"] > 0 and nm["pnl"] <= 0:
            degradation = True
    return {
        "ok": True,
        "n_old": len(old),
        "n_new": len(new),
        "old": om,
        "new": nm,
        "gap_expectancy": round(gap_exp, 6) if gap_exp is not None else None,
        "gap_sharpe": round(gap_sharpe, 6) if gap_sharpe is not None else None,
        "degradation": degradation,
        "train_frac": train_frac,
    }


__all__ = ["out_of_sample"]
