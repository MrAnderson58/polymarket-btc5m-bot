"""Per-coin and universal DNA from fingerprints."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)


FOCUS_COINS = ("BTC", "ETH", "SOL", "LINK", "XRP", "BNB", "AVAX", "DOGE", "ADA", "OP")


def _pf_val(c: dict[str, Any]) -> float:
    pf = c.get("pf")
    if pf is None and float(c.get("ev") or 0) > 0 and int(c.get("n") or 0) > 0:
        return 99.0  # infinite / no losses
    try:
        return float(pf or 0.0)
    except Exception:
        return 0.0


def coin_dna(
    rows: list[dict[str, Any]],
    assignments: dict[int, str],
    clusters: list[dict[str, Any]],
) -> dict[str, Any]:
    by_coin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        if not sym:
            continue
        by_coin[sym].append(r)

    cluster_by_id = {c["id"]: c for c in clusters}
    out_coins: dict[str, Any] = {}
    for sym in list(FOCUS_COINS) + sorted(set(by_coin) - set(FOCUS_COINS)):
        items = by_coin.get(sym) or []
        if len(items) < 30:
            continue
        fp_counts = Counter(assignments.get(int(r["trade_id"])) for r in items if assignments.get(int(r["trade_id"])))
        best = []
        worst = []
        for fp_id, n in fp_counts.most_common(30):
            if not fp_id:
                continue
            c = cluster_by_id.get(fp_id)
            if not c:
                continue
            pf_v = _pf_val(c)
            row = {"fingerprint": fp_id, "n_coin": n, "pf": c.get("pf"), "ev": c.get("ev"), "wr": c.get("wr")}
            if pf_v >= 1.2 and float(c.get("ev") or 0) > 0:
                best.append(row)
            if pf_v <= 0.8 or float(c.get("ev") or 0) < 0:
                worst.append(row)
        best.sort(key=lambda x: (_pf_val(x), int(x.get("n_coin") or 0)), reverse=True)
        worst.sort(key=lambda x: (_pf_val(x), -int(x.get("n_coin") or 0)))
        # ideal entry/exit: mode direction on best fp + MAE/MFE
        ideal_entry = best[0]["fingerprint"] if best else None
        ideal_exit = {
            "avg_mae": cluster_by_id.get(ideal_entry or "", {}).get("avg_mae") if ideal_entry else None,
            "avg_mfe": cluster_by_id.get(ideal_entry or "", {}).get("avg_mfe") if ideal_entry else None,
            "avg_hold": cluster_by_id.get(ideal_entry or "", {}).get("avg_hold") if ideal_entry else None,
        }
        perf = cluster_performance([float(r["pnl"]) for r in items])
        out_coins[sym] = {
            "n": len(items),
            "wr": perf.get("wr"),
            "pf": perf.get("pf"),
            "ev": perf.get("ev"),
            "best": best[:5],
            "worst": worst[:5],
            "ideal_entry": ideal_entry,
            "ideal_exit": ideal_exit,
        }
        if len(out_coins) >= 15:
            break
    return out_coins


def universal_dna(
    rows: list[dict[str, Any]],
    assignments: dict[int, str],
    clusters: list[dict[str, Any]],
    *,
    min_coins: int = 3,
) -> list[dict[str, Any]]:
    """Fingerprints that appear profitable across many coins."""
    cluster_by_id = {c["id"]: c for c in clusters}
    by_fp_coins: dict[str, set[str]] = defaultdict(set)
    by_fp_pnls: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        fp = assignments.get(int(r["trade_id"]))
        if not fp:
            continue
        by_fp_coins[fp].add(str(r.get("symbol") or ""))
        by_fp_pnls[fp].append(float(r["pnl"]))

    out = []
    for fp, coins in by_fp_coins.items():
        if len(coins) < min_coins:
            continue
        c = cluster_by_id.get(fp) or {}
        if _pf_val(c) < 1.2 or float(c.get("ev") or 0) <= 0:
            continue
        perf = cluster_performance(by_fp_pnls[fp])
        out.append({
            "fingerprint": fp,
            "n_coins": len(coins),
            "coins": sorted(coins)[:12],
            "n": perf["n"],
            "wr": perf["wr"],
            "pf": perf["pf"],
            "ev": perf["ev"],
        })
    out.sort(key=lambda x: (int(x["n_coins"]), _pf_val(x), int(x["n"])), reverse=True)
    return out[:25]


__all__ = ["FOCUS_COINS", "coin_dna", "universal_dna"]
