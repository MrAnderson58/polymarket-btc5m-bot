"""Discover profitable / dangerous state transitions (batch)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bot.research.market_events.signal_intelligence.market_regime_transition_v1.score import (
    score_pnls,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.states import (
    LOOKBACKS,
    build_history_slices,
    canon_state,
    lw_pattern,
    transition_vector,
)


def discover_transitions(
    ordered: list[dict[str, Any]],
    *,
    lookbacks: tuple[int, ...] = LOOKBACKS,
    min_n: int = 15,
    top_k: int = 50,
) -> dict[str, Any]:
    """
    For each lookback, bucket (from_state → to_state) and pattern→outcome pnls.
    Returns scored profitable + dangerous transitions.
    """
    # key → list of subsequent trade pnls (the transition payoff = current pnl)
    edge_pnls: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    pattern_pnls: dict[tuple[int, str], list[float]] = defaultdict(list)
    # special: consecutive LONG losses → SHORT win style
    dir_loss_to_flip: dict[str, list[float]] = defaultdict(list)

    baseline_pnls = [
        float(r.get("pnl") or r.get("pnl_pct") or 0)
        for r in ordered
    ]

    for i, cur in enumerate(ordered):
        if i < 1:
            continue
        hist = build_history_slices(ordered, i, lookbacks)
        j = cur.get("_journal")
        for lb, prior in hist.items():
            if len(prior) < min(lb, 1):
                continue
            vec = transition_vector(prior, cur, journal=j)
            key = (lb, vec["from_state"], vec["to_state"])
            pnl = float(vec.get("pnl") or 0)
            edge_pnls[key].append(pnl)
            if vec.get("pattern"):
                pattern_pnls[(lb, str(vec["pattern"]))].append(pnl)

            # 3 LONG losses → current SHORT
            if lb >= 3 and len(prior) >= 3:
                last3 = prior[-3:]
                if (
                    all(str(r.get("direction") or "").upper() == "LONG" for r in last3)
                    and all(float(r.get("pnl") or 0) < 0 for r in last3)
                    and str(cur.get("direction") or "").upper() == "SHORT"
                ):
                    dir_loss_to_flip["3LONG_LOSS→SHORT"].append(pnl)
                if (
                    all(str(r.get("direction") or "").upper() == "SHORT" for r in last3)
                    and all(float(r.get("pnl") or 0) < 0 for r in last3)
                    and str(cur.get("direction") or "").upper() == "LONG"
                ):
                    dir_loss_to_flip["3SHORT_LOSS→LONG"].append(pnl)

    profitable: list[dict[str, Any]] = []
    dangerous: list[dict[str, Any]] = []

    for (lb, frm, to), pnls in edge_pnls.items():
        if len(pnls) < min_n:
            continue
        sc = score_pnls(pnls, baseline=baseline_pnls)
        row = {
            "transition_key": f"lb{lb}:{frm}→{to}",
            "lookback": lb,
            "from_state": frm,
            "to_state": to,
            "pattern": None,
            "kind": "state_edge",
            **sc,
        }
        if float(sc.get("ev") or 0) >= 0:
            profitable.append(row)
        else:
            dangerous.append(row)

    for (lb, pat), pnls in pattern_pnls.items():
        if len(pnls) < min_n or len(pat) < 3:
            continue
        sc = score_pnls(pnls, baseline=baseline_pnls)
        row = {
            "transition_key": f"lb{lb}:pat:{pat}",
            "lookback": lb,
            "from_state": "PATTERN",
            "to_state": "NEXT",
            "pattern": pat,
            "kind": "lw_pattern",
            **sc,
        }
        if float(sc.get("ev") or 0) >= 0:
            profitable.append(row)
        else:
            dangerous.append(row)

    for name, pnls in dir_loss_to_flip.items():
        if len(pnls) < max(8, min_n // 2):
            continue
        sc = score_pnls(pnls, baseline=baseline_pnls)
        row = {
            "transition_key": name,
            "lookback": 3,
            "from_state": name.split("→")[0],
            "to_state": name.split("→")[-1],
            "pattern": None,
            "kind": "dir_flip",
            **sc,
        }
        if float(sc.get("ev") or 0) >= 0:
            profitable.append(row)
        else:
            dangerous.append(row)

    profitable.sort(key=lambda r: (bool(r.get("ready")), float(r.get("score") or 0)), reverse=True)
    dangerous.sort(key=lambda r: (float(r.get("ev") or 0), float(r.get("score") or 0)))

    return {
        "profitable": profitable[:top_k],
        "dangerous": dangerous[:top_k],
        "n_edges_scored": len(edge_pnls),
        "n_patterns_scored": len(pattern_pnls),
        "ready_profitable": [r for r in profitable if r.get("ready")][:20],
        "ready_dangerous": [r for r in dangerous if r.get("ready")][:20],
    }


__all__ = ["discover_transitions"]
