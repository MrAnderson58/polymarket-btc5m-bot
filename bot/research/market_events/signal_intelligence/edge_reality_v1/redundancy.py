"""Redundancy matrix: correlation, MI, overlap, redundancy."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.edge_reality_v1.metrics import (
    mutual_information_binary,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.signals import (
    AUDIT_MODULES,
)


def _action_code(a: str) -> float:
    aa = str(a).upper()
    if aa == "BUY":
        return 1.0
    if aa == "SELL":
        return -1.0
    return 0.0


def redundancy_matrix(
    module_actions: dict[str, list[str]],
    *,
    modules: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    mods = [m for m in (modules or AUDIT_MODULES) if m in module_actions]
    n = len(next(iter(module_actions.values()), [])) if module_actions else 0
    if n == 0 or not mods:
        return {
            "modules": mods,
            "correlation": {},
            "mutual_information": {},
            "overlap": {},
            "redundancy": {},
            "top_redundant_pairs": [],
        }
    corr: dict[str, dict[str, float]] = {}
    mi: dict[str, dict[str, float]] = {}
    overlap: dict[str, dict[str, float]] = {}
    redundancy: dict[str, dict[str, float]] = {}

    coded = {
        m: np.array([_action_code(a) for a in module_actions[m]], dtype=float)
        for m in mods
    }
    binary_take = {
        m: [str(a).upper() in ("BUY", "SELL") for a in module_actions[m]]
        for m in mods
    }

    for a in mods:
        corr[a] = {}
        mi[a] = {}
        overlap[a] = {}
        redundancy[a] = {}
        for b in mods:
            if a == b:
                corr[a][b] = 1.0
                mi[a][b] = 0.0
                overlap[a][b] = 1.0
                redundancy[a][b] = 1.0
                continue
            xa, xb = coded[a], coded[b]
            if xa.std() < 1e-12 or xb.std() < 1e-12:
                c = 0.0
            else:
                c = float(np.corrcoef(xa, xb)[0, 1])
                if not np.isfinite(c):
                    c = 0.0
            corr[a][b] = round(c, 4)
            mi_ab = mutual_information_binary(binary_take[a], binary_take[b])
            mi[a][b] = mi_ab
            # overlap: fraction of identical actions
            same = sum(
                1
                for i in range(n)
                if str(module_actions[a][i]).upper() == str(module_actions[b][i]).upper()
            )
            ov = same / n if n else 0.0
            overlap[a][b] = round(ov, 4)
            # redundancy score: high overlap + high |corr|
            redundancy[a][b] = round(0.5 * ov + 0.5 * abs(c), 4)

    # duplicated pairs
    pairs = []
    for i, a in enumerate(mods):
        for b in mods[i + 1 :]:
            pairs.append({
                "a": a,
                "b": b,
                "correlation": corr[a][b],
                "mutual_information": mi[a][b],
                "overlap": overlap[a][b],
                "redundancy": redundancy[a][b],
            })
    pairs.sort(key=lambda p: -float(p["redundancy"]))
    return {
        "modules": mods,
        "correlation": corr,
        "mutual_information": mi,
        "overlap": overlap,
        "redundancy": redundancy,
        "top_redundant_pairs": pairs[:15],
    }


__all__ = ["redundancy_matrix"]
