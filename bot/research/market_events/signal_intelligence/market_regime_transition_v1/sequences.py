"""Mine top transition chains (direction×outcome sequences)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bot.research.market_events.signal_intelligence.market_regime_transition_v1.score import (
    score_pnls,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.states import (
    direction_token,
    outcome_token,
)


def _tok(row: dict[str, Any]) -> str:
    return f"{direction_token(row)} { 'WIN' if outcome_token(row) == 'W' else ('LOSS' if outcome_token(row) == 'L' else 'BE') }"


def mine_sequences(
    ordered: list[dict[str, Any]],
    *,
    depths: tuple[int, ...] = (2, 3, 4),
    top_n: int = 100,
    min_n: int = 12,
) -> list[dict[str, Any]]:
    """
    Mine chains like LONG LOSS → LONG LOSS → SHORT WIN.
    Payoff = pnl of the trade immediately after the chain.
    """
    tokens = [_tok(r) for r in ordered]
    pnls = [float(r.get("pnl") or 0) for r in ordered]
    buckets: dict[tuple[str, ...], list[float]] = defaultdict(list)

    for depth in depths:
        for i in range(depth, len(tokens)):
            chain = tuple(tokens[i - depth : i])
            buckets[chain].append(pnls[i])

    baseline = pnls
    scored: list[dict[str, Any]] = []
    for chain, xs in buckets.items():
        if len(xs) < min_n:
            continue
        sc = score_pnls(xs, baseline=baseline)
        scored.append({
            "sequence_key": "→".join(chain),
            "chain": " → ".join(chain),
            "depth": len(chain),
            **sc,
        })
    scored.sort(key=lambda r: (bool(r.get("ready")), float(r.get("score") or 0), int(r.get("n") or 0)), reverse=True)
    return scored[:top_n]


__all__ = ["mine_sequences"]
