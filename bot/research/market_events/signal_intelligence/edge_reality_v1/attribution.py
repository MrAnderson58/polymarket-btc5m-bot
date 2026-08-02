"""Per-module attribution vs production baseline."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.edge_reality_v1.metrics import (
    delta_metrics,
    score_actions,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.signals import (
    AUDIT_MODULES,
)


def attribute_modules(
    module_actions: dict[str, list[str]],
    pnls: list[float],
) -> list[dict[str, Any]]:
    """For each module compute solo metrics and Δ vs production."""
    prod = module_actions.get("production") or ["SKIP"] * len(pnls)
    base = score_actions(prod, pnls)
    rows: list[dict[str, Any]] = []

    rows.append({
        "module": "production",
        **base,
        "delta_ev": 0.0,
        "delta_pf": 0.0,
        "delta_wr": 0.0,
        "vs_production": {
            "delta_ev": 0.0,
            "delta_pf": 0.0,
            "delta_wr": 0.0,
            "delta_mi": 0.0,
            "delta_ig": 0.0,
            "delta_f1": 0.0,
        },
        "research_value": float(base.get("f1") or 0.0),
    })

    for m in AUDIT_MODULES:
        actions = module_actions.get(m)
        if not actions:
            continue
        metrics = score_actions(actions, pnls)
        vs = delta_metrics(base, metrics)
        rows.append({
            "module": m,
            **metrics,
            "delta_ev": vs.get("delta_ev"),
            "delta_pf": vs.get("delta_pf"),
            "delta_wr": vs.get("delta_wr"),
            "vs_production": vs,
            "research_value": float(metrics.get("f1") or 0.0),
        })
    return rows


__all__ = ["attribute_modules"]
