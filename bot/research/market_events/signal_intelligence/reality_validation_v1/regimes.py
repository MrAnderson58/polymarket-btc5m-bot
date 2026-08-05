"""PART 5 — Market regime slices (research-only)."""

from __future__ import annotations

from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.reality_validation_v1.metrics import (
    basic_metrics,
    extract_pnls,
    infer_regime,
    sort_chrono,
)


REGIMES = ("bull", "bear", "range", "mixed")


def regime_slices(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = sort_chrono(trades)
    buckets: dict[str, list[dict[str, Any]]] = {r: [] for r in REGIMES}
    for t in rows:
        buckets[infer_regime(t)].append(t)

    out: dict[str, Any] = {}
    for r in REGIMES:
        m = basic_metrics(extract_pnls(buckets[r]))
        out[r] = m

    # mixed = all together already labeled; also provide "all"
    out["all"] = basic_metrics(extract_pnls(rows))

    # fragility: profitable in only one regime
    profitable = [r for r in REGIMES if (out[r].get("pnl") or 0) > 0 and (out[r].get("n") or 0) >= 5]
    fragile = len(profitable) <= 1
    return {
        "ok": True,
        "regimes": out,
        "profitable_regimes": profitable,
        "fragile": fragile,
        "counts": {r: out[r].get("n") for r in list(REGIMES) + ["all"]},
    }


__all__ = ["REGIMES", "regime_slices"]
