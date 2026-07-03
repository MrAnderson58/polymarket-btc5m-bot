"""Trading Decision Council v1 — single decision from all AI voters.

All intelligence sources vote. Council aggregates into ONE decision:
    KEEP / READY_FOR_SHADOW / PROMOTE / REJECT

No source makes decisions alone. All recommendations go through Council.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from bot.evolution.constants import (
    MIN_CONFIDENCE_PCT,
    MIN_TRADES_SINCE_CHANGE,
    SHADOW_PF_IMPROVEMENT_MIN,
)
from bot.evolution.state import EvolutionCandidate, EvolutionStatus

logger = logging.getLogger(__name__)

SUPPORTED_PARAMETERS = ("entry", "stop_loss", "trailing_activation", "trailing_distance")

PARAM_LABELS = {
    "entry": "Entry",
    "stop_loss": "Stop Loss",
    "trailing_activation": "Trailing Activation",
    "trailing_distance": "Trailing Distance",
}


@dataclass
class Vote:
    """One voter's recommendation."""

    source: str
    parameter: str | None = None
    value: float | None = None
    direction: str | None = None  # "lower" / "higher" / None
    confidence: float = 0.0
    reason: str = ""

    @property
    def is_keep(self) -> bool:
        return self.parameter is None

    @property
    def label(self) -> str:
        if self.is_keep:
            return "KEEP"
        param_label = PARAM_LABELS.get(self.parameter or "", self.parameter or "?")
        if self.value is not None:
            return f"{param_label} {self.value:.4g}"
        if self.direction:
            return f"{param_label} ({self.direction})"
        return param_label


@dataclass
class CouncilResult:
    """Final council decision with full voting record."""

    status: str  # EvolutionStatus value
    final_parameter: str | None = None
    final_from_value: float | None = None
    final_to_value: float | None = None
    confidence_pct: float = 0.0
    reason: str = ""
    votes: list[Vote] = field(default_factory=list)
    evidence_trades: int = 0
    expected_pf_pct: float = 0.0

    @property
    def final_label(self) -> str:
        if self.final_parameter is None:
            return "KEEP"
        param_label = PARAM_LABELS.get(self.final_parameter, self.final_parameter)
        if self.final_to_value is not None:
            return f"{param_label} {self.final_to_value:.4g}"
        return param_label

    def as_candidate(self) -> EvolutionCandidate | None:
        if self.final_parameter is None or self.final_to_value is None:
            return None
        return {
            "parameter": self.final_parameter,
            "from_value": self.final_from_value or 0.0,
            "to_value": self.final_to_value,
            "label": f"CHANGE {PARAM_LABELS.get(self.final_parameter, self.final_parameter).upper()}",
            "confidence_pct": self.confidence_pct,
            "evidence_trades": self.evidence_trades,
            "expected_pf_pct": self.expected_pf_pct,
            "expected_dd_pct": None,
            "walk_forward": "UNKNOWN",
            "overfit": "LOW",
        }


# ---------------------------------------------------------------------------
# Vote extraction from each source
# ---------------------------------------------------------------------------

def _vote_from_optimizer(optimizer: dict[str, Any]) -> Vote:
    param_opt = optimizer.get("parameter_optimizer", {})
    cur = param_opt.get("current", {})
    opt = param_opt.get("optimal", {})
    if not cur or not opt:
        return Vote(source="Optimizer", reason="No optimizer data")

    mapping = {
        "entry": ("entry", "entry"),
        "stop_loss": ("stop_pct", "stop_pct"),
        "trailing_activation": ("trailing_activation", "trailing_activation"),
        "trailing_distance": ("trailing_distance", "trailing_distance"),
    }

    best_param = None
    best_delta = 0.0
    best_from = None
    best_to = None

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
        if delta > best_delta:
            best_delta = delta
            best_param = param
            best_from = float(cur_val)
            best_to = float(opt_val)

    if best_param is None:
        return Vote(source="Optimizer", reason="No significant parameter change")

    direction = "lower" if best_to < best_from else "higher"
    pf_pct = float(param_opt.get("expected_improvement_pct", 0))
    return Vote(
        source="Optimizer",
        parameter=best_param,
        value=best_to,
        direction=direction,
        confidence=pf_pct,
        reason=f"{best_from:.4g} → {best_to:.4g} (PF +{pf_pct:.0f}%)",
    )


def _vote_from_scientist(scientist: dict[str, Any]) -> Vote:
    step = scientist.get("best_next_step") or {}
    if step.get("blocked"):
        return Vote(source="Scientist", reason="No validated experiment")

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
        return Vote(source="Scientist", reason="No parameter signal")

    value = step.get("suggested_value")
    if value is not None:
        value = float(value)
    confidence = float(step.get("confidence_pct", 0))
    return Vote(
        source="Scientist",
        parameter=param,
        value=value,
        confidence=confidence,
        reason=rec[:80],
    )


def _vote_from_brain(brain: dict[str, Any]) -> Vote:
    top_knowledge = brain.get("top_knowledge", [])
    if not top_knowledge:
        return Vote(source="Brain", reason="No causal signal")

    for k in top_knowledge:
        feature = str(k.get("feature", "")).lower()
        direction = str(k.get("direction", ""))
        param = None
        if "entry" in feature or "threshold" in feature:
            param = "entry"
        elif "stop" in feature:
            param = "stop_loss"
        elif "trail" in feature:
            param = "trailing_activation"
        if param:
            return Vote(
                source="Brain",
                parameter=param,
                direction=direction or None,
                confidence=float(k.get("importance", 0)),
                reason=f"{feature} → {direction}",
            )

    return Vote(source="Brain", reason="KEEP — no actionable signal")


def _vote_from_strategy_review(review: dict[str, Any]) -> Vote:
    verdict = review.get("final_verdict", {})
    change_param = verdict.get("change_parameter")
    if not change_param:
        return Vote(
            source="Strategy Review",
            reason=str(verdict.get("reason", "KEEP"))[:80],
        )

    param = _normalize_param(str(change_param))
    value = verdict.get("to_value")
    if value is not None:
        value = float(value)
    confidence = float(verdict.get("confidence_pct", 0))
    return Vote(
        source="Strategy Review",
        parameter=param,
        value=value,
        confidence=confidence,
        reason=str(verdict.get("reason", ""))[:80],
    )


def _vote_from_surgeon(surgeon: dict[str, Any]) -> Vote:
    recommendation = surgeon.get("recommendation") or {}
    param = recommendation.get("parameter")
    if not param:
        return Vote(source="Surgeon", reason="No recommendation")

    param = _normalize_param(str(param))
    value = recommendation.get("to_value")
    if value is not None:
        value = float(value)
    direction = recommendation.get("direction")
    return Vote(
        source="Surgeon",
        parameter=param,
        value=value,
        direction=direction,
        reason=str(recommendation.get("reason", ""))[:80],
    )


def _vote_from_ai_agent(ai: dict[str, Any]) -> Vote:
    decisions = ai.get("decisions") or {}
    allow = int(decisions.get("ALLOW", 0))
    skip = int(decisions.get("SKIP", 0))

    if skip > allow:
        return Vote(
            source="AI Agent",
            reason=f"SKIP ({skip}) > ALLOW ({allow}) — conservative",
        )

    patterns = ai.get("patterns") or {}
    top_signal = patterns.get("top_improvement_signal") or {}
    param = top_signal.get("parameter")
    if param:
        param = _normalize_param(str(param))
        value = top_signal.get("value")
        if value is not None:
            value = float(value)
        return Vote(
            source="AI Agent",
            parameter=param,
            value=value,
            confidence=float(top_signal.get("confidence", 0)),
            reason=f"ALLOW={allow} SKIP={skip}; pattern: {param}",
        )

    return Vote(
        source="AI Agent",
        parameter=None,
        confidence=0.0,
        reason=f"ALLOW={allow} SKIP={skip} — no specific parameter signal",
    )


# ---------------------------------------------------------------------------
# Consensus logic
# ---------------------------------------------------------------------------

def _collect_votes(sources: dict[str, Any], surgeon: dict[str, Any] | None = None) -> list[Vote]:
    votes = [
        _vote_from_optimizer(sources.get("optimizer", {})),
        _vote_from_scientist(sources.get("scientist", {})),
        _vote_from_brain(sources.get("trading_brain", {})),
        _vote_from_strategy_review(sources.get("strategy_review", {})),
        _vote_from_surgeon(surgeon or {}),
        _vote_from_ai_agent(sources.get("ai_agent", {})),
    ]
    return votes


def _find_consensus(votes: list[Vote]) -> tuple[str | None, float | None, float, str]:
    """Find the parameter with strongest agreement.

    Returns (parameter, value, confidence_pct, reason).
    """
    param_votes: dict[str, list[Vote]] = {}
    for v in votes:
        if v.parameter:
            param_votes.setdefault(v.parameter, []).append(v)

    if not param_votes:
        return None, None, 0.0, "All voters recommend KEEP"

    scored: list[tuple[str, float, list[Vote]]] = []
    for param, pv in param_votes.items():
        score = 0.0
        for v in pv:
            weight = _source_weight(v.source)
            score += weight * (1.0 + v.confidence / 100.0)
        scored.append((param, score, pv))

    scored.sort(key=lambda x: x[1], reverse=True)
    best_param, best_score, best_votes = scored[0]

    n_supporters = len(best_votes)
    total_voters = len(votes)
    confidence = (n_supporters / total_voters) * 100.0

    values = [v.value for v in best_votes if v.value is not None]
    final_value = None
    if values:
        final_value = _consensus_value(values, best_votes)

    supporters = [v.source for v in best_votes]
    non_supporters = [v.source for v in votes if v.parameter != best_param]
    reason = _build_reason(best_param, supporters, non_supporters, votes)

    return best_param, final_value, confidence, reason


def _consensus_value(values: list[float], votes: list[Vote]) -> float:
    """Pick the most conservative value when voters disagree.

    Conservative = smallest change from current (highest absolute value for
    thresholds like entry, closest to zero change magnitude).
    When multiple values compete, prefer the one with more weighted support.
    Among equal support, pick the more conservative (closer to current).
    """
    if len(values) == 1:
        return values[0]

    value_weights: dict[float, float] = {}
    for v in votes:
        if v.value is not None:
            w = _source_weight(v.source)
            value_weights[v.value] = value_weights.get(v.value, 0.0) + w

    if not value_weights:
        return values[0]

    max_weight = max(value_weights.values())
    top_values = [val for val, w in value_weights.items() if w >= max_weight * 0.7]

    if len(top_values) == 1:
        return top_values[0]

    # Multiple values have comparable support — pick most conservative
    # (smallest absolute change = largest value for entry-like params)
    return max(top_values)


def _source_weight(source: str) -> float:
    weights = {
        "Optimizer": 30.0,
        "Scientist": 25.0,
        "Strategy Review": 20.0,
        "Brain": 10.0,
        "Surgeon": 10.0,
        "AI Agent": 5.0,
    }
    return weights.get(source, 5.0)


def _build_reason(
    param: str,
    supporters: list[str],
    non_supporters: list[str],
    votes: list[Vote],
) -> str:
    param_label = PARAM_LABELS.get(param, param)
    sup_str = " и ".join(supporters)
    if not non_supporters:
        return f"Все голосуют за {param_label}."

    values = [v.value for v in votes if v.parameter == param and v.value is not None]
    unique_values = sorted(set(values))

    if len(unique_values) > 1:
        aggressive = min(unique_values)
        conservative = max(unique_values)
        aggressive_voters = [v.source for v in votes if v.value == aggressive]
        conservative_voters = [v.source for v in votes if v.value == conservative]
        if aggressive_voters and conservative_voters:
            agg_str = " и ".join(aggressive_voters)
            con_str = " и ".join(conservative_voters)
            return (
                f"{agg_str} предлагают более агрессивное изменение ({aggressive:.4g}), "
                f"но {con_str} считают достаточным {conservative:.4g}. "
                f"Выбрано консервативное значение."
            )

    non_sup_reasons = []
    for v in votes:
        if v.source in non_supporters and v.reason:
            non_sup_reasons.append(f"{v.source}: {v.reason}")

    if non_sup_reasons:
        return f"{sup_str} голосуют за {param_label}. {'; '.join(non_sup_reasons[:2])}"
    return f"{sup_str} голосуют за {param_label}."


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def convene_council(
    sources: dict[str, Any],
    *,
    surgeon: dict[str, Any] | None = None,
    shadow_state: dict[str, Any] | None = None,
) -> CouncilResult:
    """Convene the Decision Council. Returns ONE final decision.

    Called after all sources are loaded. Replaces auto_review + decision logic.
    """
    if shadow_state:
        return _council_with_shadow(sources, shadow_state)

    votes = _collect_votes(sources, surgeon)
    live = sources.get("live_sample", {})
    trades_since = int(live.get("current_since_change", 0))
    total_trades = int(live.get("total_trades", 0))

    if trades_since < MIN_TRADES_SINCE_CHANGE:
        return CouncilResult(
            status=EvolutionStatus.KEEP.value,
            reason=f"Недостаточно данных: {trades_since}/{MIN_TRADES_SINCE_CHANGE} сделок после изменения.",
            votes=votes,
            evidence_trades=total_trades,
        )

    param, value, confidence, reason = _find_consensus(votes)

    if param is None:
        return CouncilResult(
            status=EvolutionStatus.KEEP.value,
            confidence_pct=0.0,
            reason=reason,
            votes=votes,
            evidence_trades=total_trades,
        )

    from_value = _get_current_value(sources, param)

    optimizer = sources.get("optimizer", {})
    wf = optimizer.get("walk_forward", {})
    wf_ok = _walk_forward_ok(wf)
    overfit = _overfit_level(optimizer)
    n_supporters = sum(1 for v in votes if v.parameter == param)

    ready = (
        confidence >= MIN_CONFIDENCE_PCT / 2.0
        and n_supporters >= 2
        and wf_ok
        and overfit in ("LOW", "MEDIUM")
    )

    pf_pct = _expected_pf(sources, param)

    if ready:
        return CouncilResult(
            status=EvolutionStatus.READY_FOR_SHADOW.value,
            final_parameter=param,
            final_from_value=from_value,
            final_to_value=value,
            confidence_pct=confidence,
            reason=reason,
            votes=votes,
            evidence_trades=total_trades,
            expected_pf_pct=pf_pct,
        )

    return CouncilResult(
        status=EvolutionStatus.WATCH.value,
        final_parameter=param,
        final_from_value=from_value,
        final_to_value=value,
        confidence_pct=confidence,
        reason=reason,
        votes=votes,
        evidence_trades=total_trades,
        expected_pf_pct=pf_pct,
    )


def _council_with_shadow(
    sources: dict[str, Any],
    shadow: dict[str, Any],
) -> CouncilResult:
    """When a shadow experiment is active, Council reports its status."""
    raw_status = str(shadow.get("status", "")).upper()
    if raw_status == "RUNNING":
        status = EvolutionStatus.SHADOW_RUNNING.value
    elif raw_status == "PROMOTE":
        status = EvolutionStatus.SHADOW_PROMOTE.value
    elif raw_status == "REJECT":
        status = EvolutionStatus.SHADOW_REJECT.value
    else:
        status = EvolutionStatus.SHADOW_RUNNING.value

    candidate = shadow.get("candidate") or {}
    votes = _collect_votes(sources)
    return CouncilResult(
        status=status,
        final_parameter=candidate.get("parameter") or shadow.get("parameter"),
        final_from_value=shadow.get("current_value"),
        final_to_value=shadow.get("shadow_value"),
        confidence_pct=0.0,
        reason=shadow.get("reason", "Shadow experiment active"),
        votes=votes,
        evidence_trades=int((sources.get("live_sample") or {}).get("total_trades", 0)),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _get_current_value(sources: dict[str, Any], param: str) -> float | None:
    opt = sources.get("optimizer", {}).get("parameter_optimizer", {})
    cur = opt.get("current", {})
    mapping = {
        "entry": "entry",
        "stop_loss": "stop_pct",
        "trailing_activation": "trailing_activation",
        "trailing_distance": "trailing_distance",
    }
    key = mapping.get(param, param)
    val = cur.get(key)
    return float(val) if val is not None else None


def _walk_forward_ok(wf: Any) -> bool:
    if isinstance(wf, dict):
        rows = wf.get("rows", [])
        if rows and "generalizes" in rows[0]:
            return all(r.get("generalizes", False) for r in rows)
        trend = wf.get("trend", "")
        return trend in ("stable", "improving")
    return False


def _overfit_level(optimizer: dict[str, Any]) -> str:
    overfit = optimizer.get("overfit_detector") or {}
    return str(overfit.get("level", "LOW"))


def _expected_pf(sources: dict[str, Any], param: str) -> float:
    opt = sources.get("optimizer", {}).get("parameter_optimizer", {})
    return float(opt.get("expected_improvement_pct", 0))
