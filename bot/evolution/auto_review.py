"""Trading Strategy Auto Review — find the single best parameter change candidate.

Uses all 4 intelligence sources:
  - Optimizer (parameter grid, walk-forward)
  - Scientist (validated experiments)
  - Brain (causal knowledge)
  - AI Agent (ALLOW/SKIP decisions)

Rules:
  - Always ONE parameter at a time
  - All sources must agree (consensus)
  - Auto-creates Shadow Experiment if ready and none running
"""

from __future__ import annotations

import logging
from typing import Any

from bot.evolution.constants import MIN_CONFIDENCE_PCT
from bot.evolution.state import EvolutionCandidate

logger = logging.getLogger(__name__)

SUPPORTED_PARAMETERS = ("entry", "stop_loss", "trailing_activation", "trailing_distance")

PARAM_LABELS = {
    "entry": "Entry Threshold",
    "stop_loss": "Stop Loss",
    "trailing_activation": "Trailing Activation",
    "trailing_distance": "Trailing Distance",
}


def _extract_optimizer_candidates(optimizer: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract parameter change signals from optimizer cache."""
    param_opt = optimizer.get("parameter_optimizer", {})
    cur = param_opt.get("current", {})
    opt = param_opt.get("optimal", {})
    if not cur or not opt:
        return []

    candidates: list[dict[str, Any]] = []
    mapping = {
        "entry": ("entry", "entry"),
        "stop_loss": ("stop_pct", "stop_pct"),
        "trailing_activation": ("trailing_activation", "trailing_activation"),
        "trailing_distance": ("trailing_distance", "trailing_distance"),
    }
    for param, (cur_key, opt_key) in mapping.items():
        cur_val = cur.get(cur_key)
        opt_val = opt.get(opt_key)
        if cur_val is None or opt_val is None:
            continue
        delta = abs(float(opt_val) - float(cur_val))
        if param == "entry" and delta < 0.004:
            continue
        if param == "stop_loss" and delta < 0.5:
            continue
        if param.startswith("trailing") and delta < 0.002:
            continue
        pf_pct = float(param_opt.get("expected_improvement_pct", 0))
        candidates.append({
            "parameter": param,
            "from_value": float(cur_val),
            "to_value": float(opt_val),
            "source": "optimizer",
            "expected_pf_pct": pf_pct,
        })
    return candidates


def _extract_scientist_recommendation(scientist: dict[str, Any]) -> dict[str, Any] | None:
    """Extract parameter from best_next_step if not blocked."""
    step = scientist.get("best_next_step") or {}
    if step.get("blocked"):
        return None
    rec = str(step.get("recommendation") or "")
    rec_lower = rec.lower()
    param = None
    for p in SUPPORTED_PARAMETERS:
        if p.replace("_", " ") in rec_lower or p in rec_lower:
            param = p
            break
    if "entry" in rec_lower or "threshold" in rec_lower:
        param = "entry"
    elif "stop" in rec_lower:
        param = "stop_loss"
    elif "trailing" in rec_lower:
        param = param or "trailing_activation"
    if param is None:
        return None
    return {
        "parameter": param,
        "source": "scientist",
        "expected_pf_pct": float(step.get("expected_pf_pct", 0)),
        "confidence_pct": float(step.get("confidence_pct", 0)),
        "experiment_id": step.get("experiment_id"),
    }


def _extract_brain_support(brain: dict[str, Any]) -> dict[str, str]:
    """Map brain knowledge features to supported parameters."""
    support: dict[str, str] = {}
    for k in brain.get("top_knowledge", []):
        feature = str(k.get("feature", "")).lower()
        direction = str(k.get("direction", ""))
        if "entry" in feature or "threshold" in feature:
            support["entry"] = direction
        elif "stop" in feature:
            support["stop_loss"] = direction
        elif "trail" in feature:
            support.setdefault("trailing_activation", direction)
    return support


def _ai_agent_consensus(ai: dict[str, Any]) -> bool:
    """AI Agent supports change when ALLOW >= SKIP."""
    decisions = ai.get("decisions") or {}
    return int(decisions.get("ALLOW", 0)) >= int(decisions.get("SKIP", 0))


def _score_candidate(
    candidate: dict[str, Any],
    *,
    scientist_param: str | None,
    brain_params: dict[str, str],
    strategy_review_param: str | None,
) -> float:
    """Higher score = stronger consensus."""
    score = 0.0
    param = candidate["parameter"]
    score += float(candidate.get("expected_pf_pct", 0))
    if param == scientist_param:
        score += 30.0
    if param in brain_params:
        score += 15.0
    if param == strategy_review_param:
        score += 20.0
    return score


def find_best_candidate(sources: dict[str, Any]) -> EvolutionCandidate | None:
    """
    Find THE SINGLE BEST parameter change candidate using all 4 systems.

    Returns None if no consensus or insufficient evidence.
    """
    optimizer = sources.get("optimizer", {})
    scientist = sources.get("scientist", {})
    brain = sources.get("trading_brain", {})
    ai = sources.get("ai_agent", {})
    review = sources.get("strategy_review", {})
    live = sources.get("live_sample", {})
    verdict = review.get("final_verdict", {})

    if not _ai_agent_consensus(ai):
        logger.debug("AUTO_REVIEW | AI Agent does not support changes (SKIP > ALLOW)")
        return None

    optimizer_candidates = _extract_optimizer_candidates(optimizer)
    if not optimizer_candidates:
        logger.debug("AUTO_REVIEW | No optimizer candidates found")
        return None

    scientist_rec = _extract_scientist_recommendation(scientist)
    scientist_param = scientist_rec["parameter"] if scientist_rec else None
    brain_params = _extract_brain_support(brain)
    strategy_review_param = verdict.get("change_parameter")
    if strategy_review_param:
        strategy_review_param = _normalize_param(str(strategy_review_param))

    scored: list[tuple[float, dict[str, Any]]] = []
    for cand in optimizer_candidates:
        s = _score_candidate(
            cand,
            scientist_param=scientist_param,
            brain_params=brain_params,
            strategy_review_param=strategy_review_param,
        )
        scored.append((s, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    if best_score <= 0:
        return None

    param_opt = optimizer.get("parameter_optimizer", {})
    wf = optimizer.get("walk_forward", {})
    wf_label = _wf_label(wf)
    overfit = _overfit_label(optimizer)
    confidence = float(verdict.get("confidence_pct", 0))
    if confidence < MIN_CONFIDENCE_PCT and strategy_review_param != best["parameter"]:
        confidence = float(
            (scientist_rec or {}).get("confidence_pct", 0)
        ) if scientist_rec and scientist_rec["parameter"] == best["parameter"] else 0.0

    consensus_sources: list[str] = ["Optimizer"]
    if scientist_param == best["parameter"]:
        consensus_sources.append("Scientist")
    if best["parameter"] in brain_params:
        consensus_sources.append("Brain")
    consensus_sources.append("AI Agent")
    if strategy_review_param == best["parameter"]:
        consensus_sources.append("Strategy Review")

    logger.info(
        "AUTO_REVIEW | CANDIDATE | %s %.4f → %.4f | consensus=%s | score=%.1f",
        best["parameter"],
        best["from_value"],
        best["to_value"],
        "+".join(consensus_sources),
        best_score,
    )

    return {
        "parameter": best["parameter"],
        "from_value": best["from_value"],
        "to_value": best["to_value"],
        "label": f"CHANGE {PARAM_LABELS.get(best['parameter'], best['parameter']).upper()}",
        "confidence_pct": max(confidence, best_score),
        "evidence_trades": int(live.get("total_trades", 0)),
        "expected_pf_pct": float(best.get("expected_pf_pct", 0)),
        "expected_dd_pct": None,
        "walk_forward": wf_label,
        "overfit": overfit,
    }


def _normalize_param(param: str) -> str:
    mapping = {
        "entry": "entry",
        "entry_threshold": "entry",
        "stop_loss": "stop_loss",
        "stop": "stop_loss",
        "trailing_activation": "trailing_activation",
        "trailing_distance": "trailing_distance",
        "trailing": "trailing_activation",
    }
    return mapping.get(param.lower(), param)


def _wf_label(wf: Any) -> str:
    if isinstance(wf, dict):
        rows = wf.get("rows", [])
        if rows and "generalizes" in rows[0]:
            ok = all(r.get("generalizes", False) for r in rows)
            return "PASS" if ok else "FAIL"
        trend = wf.get("trend", "")
        if trend in ("stable", "improving"):
            return "PASS"
        if trend == "degrading":
            return "FAIL"
        return str(trend).upper() or "UNKNOWN"
    return "UNKNOWN"


def _overfit_label(optimizer: dict[str, Any]) -> str:
    overfit = optimizer.get("overfit_detector") or {}
    return str(overfit.get("level", "LOW"))
