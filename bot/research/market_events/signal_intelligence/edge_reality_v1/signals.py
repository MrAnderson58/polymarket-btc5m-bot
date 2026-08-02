"""Module signal extractors for Edge Reality Audit V1 (read-only)."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
    collect_opinions,
    opinion_from_alpha,
    opinion_from_causality,
    opinion_from_edge,
    opinion_from_feature_store,
    opinion_from_optimizer,
    opinion_from_replay,
    opinion_from_validation,
)

# Audit modules (user-specified set). Research-only.
AUDIT_MODULES: tuple[str, ...] = (
    "replay",
    "edge",
    "alpha",
    "optimizer",
    "brain",
    "causality",
    "evolution",
    "features",
    "validation",
)

INCREMENTAL_ORDER: tuple[str, ...] = (
    "replay",
    "edge",
    "alpha",
    "causality",
    "brain",
    "evolution",
)


def production_signal(trade: dict[str, Any]) -> dict[str, Any]:
    """Production baseline: gate PASS → take direction; else SKIP."""
    gate = str(trade.get("gate_decision") or trade.get("gate") or "").upper()
    direction = str(trade.get("direction") or "").upper()
    pass_like = gate in {
        "PASS", "ALLOWED", "OPEN", "OK", "ACCEPT", "ACCEPTED",
        "REGIME_EXPLORE", "COLD_START",
    } or (not gate and trade.get("pnl") is not None)
    reject = any(x in gate for x in ("REJECT", "BLOCK", "FAIL", "DENY", "INSUFFICIENT", "SKIP"))
    if reject:
        action = "SKIP"
        conf = 0.7
    elif pass_like:
        if direction in ("LONG", "BUY", "UP"):
            action = "BUY"
        elif direction in ("SHORT", "SELL", "DOWN"):
            action = "SELL"
        else:
            action = "HOLD"
        conf = 0.6
    else:
        action = "SKIP"
        conf = 0.5
    return {"module": "production", "action": action, "confidence": conf, "score": 0.0}


def _op_to_signal(op: dict[str, Any], *, name: str | None = None) -> dict[str, Any]:
    d = str(op.get("direction") or "HOLD").upper()
    if d == "HOLD":
        action = "SKIP"
    elif d in ("BUY", "SELL"):
        action = d
    elif d == "NO_TRADE":
        action = "SKIP"
    else:
        action = "SKIP"
    return {
        "module": name or str(op.get("module") or ""),
        "action": action,
        "confidence": float(op.get("confidence") or 0.0),
        "score": float(op.get("edge") or 0.0),
    }


def evolution_signal(trade: dict[str, Any], evo_by_key: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """Map signal-evolution library status → action."""
    evo_by_key = evo_by_key or {}
    symbol = str(trade.get("symbol") or "")
    direction = str(trade.get("direction") or "")
    regime = str(trade.get("regime") or "RANGE")
    keys = [
        f"lake:{symbol}|{direction}|{regime}",
        f"lake:regime:{regime}|{direction}",
        f"g31_family:{direction}",
        f"s40:{trade.get('s40_signal_type') or 'validation_signal'}",
    ]
    hit = None
    for k in keys:
        if k in evo_by_key:
            hit = evo_by_key[k]
            break
    if not hit:
        # soft: use any PEAK/GROWTH average tilt via trade features
        return {"module": "evolution", "action": "SKIP", "confidence": 0.25, "score": 0.0}
    status = str(hit.get("status") or "").upper()
    score = float(hit.get("score") or 0.0)
    conf = float(hit.get("confidence") or 0.4)
    if status in ("PEAK", "GROWTH") and score >= 55:
        d = direction.upper()
        action = "BUY" if d in ("LONG", "BUY") else ("SELL" if d in ("SHORT", "SELL") else "SKIP")
    elif status in ("DECAY", "DEAD"):
        action = "SKIP"
    else:
        action = "SKIP"
    return {"module": "evolution", "action": action, "confidence": conf, "score": score}


def brain_signal(trade: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Fused brain decision (read-only call into brain fusion; does not modify Brain)."""
    from bot.research.market_events.signal_intelligence.market_brain_v1.fusion import (
        bayesian_fusion,
        detect_conflict,
    )

    tid = int(trade.get("trade_id") or trade.get("id") or 0)
    opinions = collect_opinions(
        trade,
        replay=ctx.get("replays", {}).get(tid),
        edges=ctx.get("edges") or [],
        causal=ctx.get("causality", {}).get(tid),
        optimizer_state=ctx.get("optimizer_state") or {},
        experiments=ctx.get("experiments") or [],
    )
    conflict = detect_conflict(opinions)
    fusion = bayesian_fusion(opinions, conflict=conflict)
    decision = str(fusion.get("decision") or "HOLD").upper()
    action = "SKIP" if decision in ("HOLD", "NO_TRADE") else decision
    return {
        "module": "brain",
        "action": action,
        "confidence": float(fusion.get("confidence_raw") or 0.0),
        "score": float(fusion.get("expected_ev") or 0.0),
    }


def collect_module_signals(
    trade: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return {module: signal} for all audit modules + production."""
    tid = int(trade.get("trade_id") or trade.get("id") or 0)
    replay = (ctx.get("replays") or {}).get(tid)
    edges = ctx.get("edges") or []
    causal = (ctx.get("causality") or {}).get(tid)
    opt = ctx.get("optimizer_state") or {}
    evo = ctx.get("evolution") or {}

    out: dict[str, dict[str, Any]] = {
        "production": production_signal(trade),
        "replay": _op_to_signal(opinion_from_replay(trade, replay), name="replay"),
        "edge": _op_to_signal(opinion_from_edge(trade, edges), name="edge"),
        "alpha": _op_to_signal(opinion_from_alpha(trade), name="alpha"),
        "optimizer": _op_to_signal(opinion_from_optimizer(trade, opt), name="optimizer"),
        "causality": _op_to_signal(opinion_from_causality(trade, causal), name="causality"),
        "features": _op_to_signal(opinion_from_feature_store(trade), name="features"),
        "validation": _op_to_signal(opinion_from_validation(trade), name="validation"),
        "evolution": evolution_signal(trade, evo),
        "brain": brain_signal(trade, ctx),
    }
    return out


def signal_correct(action: str, pnl: float) -> bool:
    a = str(action or "").upper()
    if a == "BUY":
        return pnl > 0
    if a == "SELL":
        return pnl < 0
    # SKIP/HOLD correct if trade lost or flat
    return pnl <= 0


def realized_ev(action: str, pnl: float) -> float:
    a = str(action or "").upper()
    if a == "BUY":
        return float(pnl)
    if a == "SELL":
        return -float(pnl)
    return 0.0


__all__ = [
    "AUDIT_MODULES",
    "INCREMENTAL_ORDER",
    "collect_module_signals",
    "production_signal",
    "realized_ev",
    "signal_correct",
]
