"""Verify PF = GrossProfit / GrossLoss without duplicates."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)

Predicate = Callable[[dict[str, Any]], bool]


def verify_profit_factor(pnls: Sequence[float]) -> dict[str, Any]:
    """Confirm PF formula on a pnl series (no duplicate counting)."""
    xs = [float(p) for p in pnls]
    met = trade_metrics(xs)
    gp = float(met.get("gross_profit") or 0.0)
    gl = float(met.get("gross_loss") or 0.0)
    pf = met.get("pf")
    expected: float | None
    if gl > 1e-12:
        expected = round(gp / gl, 4)
    elif gp > 0:
        expected = None  # infinite
    else:
        expected = 0.0

    if expected is None:
        ok = pf is None and bool(met.get("pf_inf"))
    elif pf is None:
        ok = False
    else:
        ok = abs(float(pf) - float(expected)) < 1e-9

    return {
        "ok": ok,
        "n": met["n"],
        "gross_profit": met.get("gross_profit"),
        "gross_loss": met.get("gross_loss"),
        "avg_win": met.get("avg_win"),
        "avg_loss": met.get("avg_loss"),
        "pf": pf,
        "pf_inf": met.get("pf_inf"),
        "wr": met.get("wr"),
        "ev": met.get("ev"),
        "expected_pf": expected,
        "formula": "GrossProfit / GrossLoss",
        "duplicates": 0,
    }


def verify_rule_pf(
    rows: list[dict[str, Any]],
    pred: Predicate,
    *,
    trade_id_key: str = "trade_id",
) -> dict[str, Any]:
    """Apply predicate once per unique trade_id (fallback: object id)."""
    seen: set[Any] = set()
    pnls: list[float] = []
    dupes = 0
    for r in rows:
        if not pred(r):
            continue
        tid = r.get(trade_id_key)
        if tid is None:
            tid = r.get("id")
        if tid is None:
            tid = id(r)
        if tid in seen:
            dupes += 1
            continue
        seen.add(tid)
        try:
            pnls.append(float(r["pnl"]))
        except Exception:
            continue
    out = verify_profit_factor(pnls)
    out["duplicates"] = dupes
    out["ok"] = bool(out["ok"]) and dupes == 0
    return out


__all__ = ["verify_profit_factor", "verify_rule_pf"]
