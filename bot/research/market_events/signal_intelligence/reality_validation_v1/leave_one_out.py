"""PART 6–7 — Leave-one-coin / leave-one-month out (research-only)."""

from __future__ import annotations

from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.reality_validation_v1.metrics import (
    basic_metrics,
    extract_pnls,
    month_key,
    sort_chrono,
    symbol_of,
)


def leave_one_coin_out(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = sort_chrono(trades)
    by_coin: dict[str, list[dict[str, Any]]] = {}
    for t in rows:
        by_coin.setdefault(symbol_of(t), []).append(t)
    coins = sorted(by_coin.keys())
    if len(coins) < 2:
        return {
            "ok": False,
            "reason": "need_2_plus_coins",
            "coins": coins,
            "folds": [],
        }
    base = basic_metrics(extract_pnls(rows))
    folds: list[dict[str, Any]] = []
    for c in coins:
        kept = [t for t in rows if symbol_of(t) != c]
        m = basic_metrics(extract_pnls(kept))
        folds.append({
            "removed": c,
            "n_removed": len(by_coin[c]),
            "n_kept": len(kept),
            **{f"kept_{k}": m.get(k) for k in ("pnl", "wr", "sharpe", "pf", "max_dd")},
        })
    # fragility: removing one coin flips pnl sign
    flips = 0
    base_pos = (base.get("pnl") or 0) > 0
    for f in folds:
        kept_pos = (f.get("kept_pnl") or 0) > 0
        if base_pos != kept_pos:
            flips += 1
    return {
        "ok": True,
        "base": base,
        "coins": coins,
        "folds": folds,
        "sign_flips": flips,
        "fragile": flips > 0,
    }


def leave_one_month_out(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = sort_chrono(trades)
    by_m: dict[str, list[dict[str, Any]]] = {}
    for t in rows:
        by_m.setdefault(month_key(int(t.get("opened_at") or 0)), []).append(t)
    months = sorted(k for k in by_m if k != "unknown")
    if len(months) < 2:
        return {
            "ok": False,
            "reason": "need_2_plus_months",
            "months": months,
            "folds": [],
        }
    base = basic_metrics(extract_pnls(rows))
    folds: list[dict[str, Any]] = []
    for mkey in months:
        kept = [t for t in rows if month_key(int(t.get("opened_at") or 0)) != mkey]
        m = basic_metrics(extract_pnls(kept))
        folds.append({
            "removed": mkey,
            "n_removed": len(by_m[mkey]),
            "n_kept": len(kept),
            **{f"kept_{k}": m.get(k) for k in ("pnl", "wr", "sharpe", "pf", "max_dd")},
        })
    flips = 0
    base_pos = (base.get("pnl") or 0) > 0
    for f in folds:
        if base_pos != ((f.get("kept_pnl") or 0) > 0):
            flips += 1
    return {
        "ok": True,
        "base": base,
        "months": months,
        "folds": folds,
        "sign_flips": flips,
        "fragile": flips > 0,
    }


__all__ = ["leave_one_coin_out", "leave_one_month_out"]
