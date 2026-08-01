"""Module opinion collectors for Adaptive Market Brain V1."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

MODULE_NAMES: tuple[str, ...] = (
    "lake",
    "replay",
    "edge",
    "alpha",
    "causality",
    "optimizer",
    "validation",
    "feature_store",
    "experiments",
)

# Prior weights (sum ≈ 1.0) — research defaults, not trading knobs.
DEFAULT_WEIGHTS: dict[str, float] = {
    "lake": 0.08,
    "replay": 0.20,
    "edge": 0.18,
    "alpha": 0.12,
    "causality": 0.15,
    "optimizer": 0.10,
    "validation": 0.08,
    "feature_store": 0.05,
    "experiments": 0.04,
}


def _dir_from_pnl(pnl: float | None, *, thr: float = 0.0) -> str:
    if pnl is None:
        return "HOLD"
    if float(pnl) > thr:
        return "BUY"
    if float(pnl) < -thr:
        return "SELL"
    return "HOLD"


def _opinion(
    *,
    module: str,
    direction: str,
    confidence: float,
    edge: float = 0.0,
    quality: float = 0.5,
    weight: float | None = None,
    reasons: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    d = str(direction or "HOLD").upper()
    if d not in ("BUY", "SELL", "HOLD"):
        d = "HOLD"
    return {
        "module": module,
        "direction": d,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
        "edge": round(float(edge), 4),
        "quality": round(max(0.0, min(1.0, float(quality))), 4),
        "weight": float(weight if weight is not None else DEFAULT_WEIGHTS.get(module, 0.05)),
        "reasons": reasons or [],
        "meta": meta or {},
    }


def opinion_from_lake(trade: dict[str, Any]) -> dict[str, Any]:
    pnl = _safe_float(trade.get("pnl"))
    conf = abs(float(pnl or 0.0))
    conf = min(1.0, conf / 5.0) if conf else 0.35
    gate = str(trade.get("gate_decision") or trade.get("gate") or "").upper()
    regime = str(trade.get("regime") or "")
    reasons = []
    if gate:
        reasons.append(f"gate={gate}")
    if regime:
        reasons.append(f"regime={regime}")
    # Prospective lake signal uses features, not future pnl for direction in live;
    # for historical brain test we use feature tilt.
    rsi = _safe_float(trade.get("rsi"))
    funding = _safe_float(trade.get("funding"))
    score = 0.0
    if rsi is not None:
        score += (50.0 - float(rsi)) / 50.0  # oversold → BUY tilt
    if funding is not None:
        # Clamp funding tilt — raw funding can be large and blow up EV
        score += max(-2.0, min(2.0, -float(funding) * 500.0))
    score = max(-3.0, min(3.0, score))
    if score > 0.15:
        direction = "BUY"
    elif score < -0.15:
        direction = "SELL"
    else:
        direction = "HOLD"
    return _opinion(
        module="lake",
        direction=direction,
        confidence=max(0.3, min(0.85, abs(score))),
        edge=round(score * 10.0, 4),
        quality=0.55,
        reasons=reasons or ["lake_features"],
        meta={"score": round(score, 4)},
    )


def opinion_from_replay(trade: dict[str, Any], replay: dict[str, Any] | None) -> dict[str, Any]:
    if not replay:
        return _opinion(module="replay", direction="HOLD", confidence=0.2, quality=0.2, reasons=["replay_missing"])
    q = float(replay.get("quality") or 0.0)
    sim_ids = replay.get("similar_ids") or []
    if isinstance(sim_ids, str):
        try:
            import json
            sim_ids = json.loads(sim_ids)
        except Exception:
            sim_ids = []
    sim_n = len(sim_ids) if isinstance(sim_ids, list) else 0
    # Similarity strength proxy
    sim_score = min(1.0, 0.5 + 0.005 * sim_n)
    liq = replay.get("liquidity") or {}
    vol_exp = _safe_float(liq.get("volume_expansion")) or 0.0
    direction = "BUY" if vol_exp >= 0 else "SELL"
    if abs(vol_exp) < 0.01:
        # fall back to lake tilt
        base = opinion_from_lake(trade)
        direction = base["direction"]
    conf = min(1.0, 0.4 + 0.5 * q + 0.1 * sim_score)
    return _opinion(
        module="replay",
        direction=direction,
        confidence=conf,
        edge=float(vol_exp) * 20.0,
        quality=max(q, 0.3),
        reasons=[
            f"replay_quality={q:.2f}",
            f"similar_n={sim_n}",
            f"volume_expansion={vol_exp}",
        ],
        meta={"similarity": round(sim_score, 4), "similar_n": sim_n},
    )


def opinion_from_edge(trade: dict[str, Any], edges: list[dict[str, Any]]) -> dict[str, Any]:
    if not edges:
        return _opinion(module="edge", direction="HOLD", confidence=0.25, quality=0.2, reasons=["edge_library_empty"])
    # Prefer edges matching symbol/regime; otherwise soft library prior (lower conf)
    sym = str(trade.get("symbol") or "").upper()
    regime = str(trade.get("regime") or "").upper()
    best = None
    best_s = -1.0
    matched = False
    for e in edges:
        score = float(e.get("quality_score") or e.get("expectancy") or 0.0)
        rule = str(e.get("rule") or "")
        local_match = False
        if sym and sym in rule.upper():
            score += 5
            local_match = True
        if regime and regime in str(e.get("regime") or "").upper():
            score += 3
            local_match = True
        if score > best_s:
            best_s = score
            best = e
            matched = local_match
    assert best is not None
    ev = _safe_float(best.get("expectancy")) or 0.0
    qs = _safe_float(best.get("quality_score")) or 50.0
    direction = "BUY" if ev >= 0 else "SELL"
    conf = min(0.95, max(0.3, qs / 100.0))
    quality = min(1.0, qs / 100.0)
    if not matched:
        # Global library prior — damp so it cannot single-handedly veto fusion
        conf = min(conf, 0.45)
        quality = min(quality, 0.35)
    return _opinion(
        module="edge",
        direction=direction,
        confidence=conf,
        edge=float(ev) * 10.0 if abs(ev) < 5 else float(ev),
        quality=quality,
        reasons=[
            f"edge_rule={str(best.get('rule') or '')[:80]}",
            f"edge_score={qs}",
            "edge_matched" if matched else "edge_global_prior",
        ],
        meta={"expectancy": ev, "pf": best.get("pf"), "status": best.get("status"), "matched": matched},
    )


def opinion_from_alpha(trade: dict[str, Any]) -> dict[str, Any]:
    alpha = trade.get("alpha_labels") if isinstance(trade.get("alpha_labels"), dict) else {}
    disc = alpha.get("discovery") if isinstance(alpha.get("discovery"), dict) else {}
    cluster = str(disc.get("cluster") or alpha.get("cluster") or trade.get("alpha_cluster") or "")
    edge_score = _safe_float(disc.get("edge_score") or alpha.get("edge_score")) or 0.0
    status = str(disc.get("status") or alpha.get("status") or "").upper()
    if not cluster and edge_score == 0:
        # soft signal from news/ai
        ai = _safe_float(trade.get("ai_score")) or 0.0
        news = _safe_float(trade.get("news_score")) or 0.0
        s = ai + news
        direction = "BUY" if s > 0.3 else ("SELL" if s < -0.1 else "HOLD")
        return _opinion(
            module="alpha",
            direction=direction,
            confidence=min(0.7, 0.3 + abs(s)),
            edge=s * 5.0,
            quality=0.4,
            reasons=["alpha_soft_ai_news"],
        )
    direction = "BUY" if edge_score >= 0 else "SELL"
    if status in ("REJECT", "FAIL"):
        direction = "HOLD"
    conf = min(0.9, 0.4 + abs(edge_score) / 10.0)
    return _opinion(
        module="alpha",
        direction=direction,
        confidence=conf,
        edge=float(edge_score),
        quality=0.55 if cluster else 0.35,
        reasons=[f"alpha_cluster={cluster or '-'}", f"edge_score={edge_score}"],
        meta={"cluster": cluster, "status": status},
    )


def opinion_from_causality(trade: dict[str, Any], causal: dict[str, Any] | None) -> dict[str, Any]:
    pnl = _safe_float(trade.get("pnl"))
    if not causal:
        funding = abs(_safe_float(trade.get("funding")) or 0.0)
        atr = abs(_safe_float(trade.get("atr_pct") or trade.get("atr")) or 0.0)
        primary = "funding" if funding > atr * 0.0001 else "atr"
        conf = 0.35
        if pnl is None:
            direction = "HOLD"
        else:
            direction = "BUY" if pnl > 0 else ("SELL" if pnl < 0 else "HOLD")
        return _opinion(
            module="causality",
            direction=direction,
            confidence=conf,
            edge=5.0 if direction == "BUY" else (-5.0 if direction == "SELL" else 0.0),
            quality=0.3,
            reasons=[f"inferred_cause={primary}"],
        )
    primary = str(causal.get("primary_cause") or "noise")
    cluster = str(causal.get("causal_cluster") or "")
    conf = float(causal.get("confidence") or 0.4)
    # Causality explains *why* an outcome happened — direction follows explained PnL,
    # not a hard-coded cause→BUY map (which systematically fights lake/replay).
    if pnl is not None and abs(pnl) > 1e-9:
        direction = "BUY" if pnl > 0 else "SELL"
    else:
        direction = "HOLD"
    return _opinion(
        module="causality",
        direction=direction,
        confidence=max(0.25, min(0.95, conf)),
        edge=(conf * 15.0) if direction == "BUY" else ((-conf * 15.0) if direction == "SELL" else 0.0),
        quality=float(causal.get("quality") or 0.5),
        reasons=[f"primary_cause={primary}", f"cluster={cluster}", f"explained_pnl={pnl}"],
        meta={"primary_cause": primary, "cluster": cluster},
    )


def opinion_from_optimizer(trade: dict[str, Any], opt_state: dict[str, Any] | None) -> dict[str, Any]:
    opt = opt_state or {}
    if isinstance(trade.get("optimizer_state"), dict) and trade.get("optimizer_state"):
        opt = {**opt, **trade["optimizer_state"]}
    if not opt:
        return _opinion(module="optimizer", direction="HOLD", confidence=0.3, quality=0.25, reasons=["optimizer_unknown"])
    # Heuristic: presence of keys suggesting approve
    keys = " ".join(str(k).lower() for k in opt.keys())
    vals = " ".join(str(v).lower() for v in list(opt.values())[:10])
    blob = keys + " " + vals
    if any(x in blob for x in ("approve", "enable", "pass", "buy", "long")):
        direction = "BUY"
        conf = 0.65
    elif any(x in blob for x in ("block", "reject", "disable", "sell", "short")):
        direction = "SELL"
        conf = 0.6
    else:
        direction = "HOLD"
        conf = 0.45
    return _opinion(
        module="optimizer",
        direction=direction,
        confidence=conf,
        edge=5.0 if direction == "BUY" else (-5.0 if direction == "SELL" else 0.0),
        quality=0.5,
        reasons=["optimizer_state"],
        meta={"keys": list(opt.keys())[:5]},
    )


def opinion_from_validation(trade: dict[str, Any]) -> dict[str, Any]:
    alpha = trade.get("alpha_labels") if isinstance(trade.get("alpha_labels"), dict) else {}
    val = alpha.get("validation") if isinstance(alpha.get("validation"), dict) else {}
    status = str(val.get("validation_status") or val.get("status") or "").upper()
    score = _safe_float(val.get("score")) or 0.0
    if status in ("PASS", "OK", "VALID"):
        direction = "BUY"
        conf = min(0.9, 0.55 + abs(score) / 10.0)
    elif status in ("FAIL", "REJECT"):
        direction = "HOLD"
        conf = 0.7
    else:
        # use result as weak validation of historical path only for meta
        direction = "HOLD"
        conf = 0.35
    return _opinion(
        module="validation",
        direction=direction,
        confidence=conf,
        edge=score,
        quality=0.5 if status else 0.3,
        reasons=[f"validation_status={status or 'unknown'}"],
        meta={"status": status, "score": score},
    )


def opinion_from_feature_store(trade: dict[str, Any]) -> dict[str, Any]:
    # Feature completeness + tilt
    feats = ("rsi", "atr_pct", "funding", "oi_delta", "fear_greed", "macd", "adx", "trend")
    present = sum(1 for f in feats if _safe_float(trade.get(f)) is not None)
    quality = present / len(feats)
    rsi = _safe_float(trade.get("rsi"))
    trend = _safe_float(trade.get("trend")) or 0.0
    if rsi is not None and rsi < 35 and trend <= 0:
        direction = "BUY"
        conf = 0.55 + 0.3 * quality
    elif rsi is not None and rsi > 65 and trend >= 0:
        direction = "SELL"
        conf = 0.55 + 0.3 * quality
    else:
        direction = "HOLD"
        conf = 0.35 + 0.2 * quality
    return _opinion(
        module="feature_store",
        direction=direction,
        confidence=min(0.85, conf),
        edge=(50 - (rsi or 50)) / 5.0,
        quality=quality,
        reasons=[f"feature_coverage={quality:.2f}"],
    )


def opinion_from_experiments(trade: dict[str, Any], experiments: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not experiments:
        return _opinion(module="experiments", direction="HOLD", confidence=0.25, quality=0.2, reasons=["no_experiments"])
    # Prefer recent status=PASS/WIN experiments
    score = 0.0
    for e in experiments[:5]:
        st = str(e.get("status") or "").upper()
        if st in ("PASS", "WIN", "SUCCESS", "ACTIVE"):
            score += 1.0
        elif st in ("FAIL", "LOSS"):
            score -= 1.0
    direction = "BUY" if score > 0 else ("SELL" if score < 0 else "HOLD")
    conf = min(0.75, 0.3 + 0.15 * abs(score))
    return _opinion(
        module="experiments",
        direction=direction,
        confidence=conf,
        edge=score * 3.0,
        quality=0.4,
        reasons=[f"experiment_score={score}"],
    )


def collect_opinions(
    trade: dict[str, Any],
    *,
    replay: dict[str, Any] | None = None,
    edges: list[dict[str, Any]] | None = None,
    causal: dict[str, Any] | None = None,
    optimizer_state: dict[str, Any] | None = None,
    experiments: list[dict[str, Any]] | None = None,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    ops = [
        opinion_from_lake(trade),
        opinion_from_replay(trade, replay),
        opinion_from_edge(trade, edges or []),
        opinion_from_alpha(trade),
        opinion_from_causality(trade, causal),
        opinion_from_optimizer(trade, optimizer_state),
        opinion_from_validation(trade),
        opinion_from_feature_store(trade),
        opinion_from_experiments(trade, experiments),
    ]
    for o in ops:
        o["weight"] = float(w.get(o["module"], o["weight"]))
    return ops


__all__ = [
    "DEFAULT_WEIGHTS",
    "MODULE_NAMES",
    "collect_opinions",
    "opinion_from_alpha",
    "opinion_from_causality",
    "opinion_from_edge",
    "opinion_from_experiments",
    "opinion_from_feature_store",
    "opinion_from_lake",
    "opinion_from_optimizer",
    "opinion_from_replay",
    "opinion_from_validation",
]
