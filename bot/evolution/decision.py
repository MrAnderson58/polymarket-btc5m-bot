"""Evolution decision engine — reads precomputed sources only."""

from __future__ import annotations

from typing import Any

from bot.evolution.constants import (
    EVOLUTION_VERSION,
    MIN_CONFIDENCE_PCT,
    MIN_TRADES_SINCE_CHANGE,
)
from bot.evolution.state import (
    EvolutionCandidate,
    EvolutionResult,
    EvolutionStatus,
)


def _walk_forward_label(wf: Any) -> tuple[str, bool]:
    if isinstance(wf, dict):
        trend = wf.get("trend", "insufficient_data")
        rows = wf.get("rows", [])
        if rows and "generalizes" in rows[0]:
            ok = all(r.get("generalizes", False) for r in rows)
            return ("PASS" if ok else "FAIL", ok)
        if trend in ("stable", "improving"):
            return ("PASS", True)
        if trend == "degrading":
            return ("FAIL", False)
        return (str(trend).upper(), trend not in ("degrading",))
    return ("UNKNOWN", False)


def _overfit_level(sources: dict[str, Any]) -> str:
    opt = sources.get("optimizer", {})
    overfit = opt.get("overfit_detector") or {}
    if overfit.get("level"):
        return str(overfit["level"])
    review = sources.get("strategy_review", {})
    ctx_overfit = (review.get("context") or {}).get("overfit") or {}
    return str(ctx_overfit.get("level", "LOW"))


def _extract_candidate(
    sources: dict[str, Any],
) -> EvolutionCandidate | None:
    """Find the single best parameter change using all 4 intelligence sources."""
    from bot.evolution.auto_review import find_best_candidate

    return find_best_candidate(sources)



def _shadow_status_from_state(shadow: dict[str, Any]) -> EvolutionStatus | None:
    raw = str(shadow.get("status", "")).upper()
    mapping = {
        "RUNNING": EvolutionStatus.SHADOW_RUNNING,
        "PROMOTE": EvolutionStatus.SHADOW_PROMOTE,
        "REJECT": EvolutionStatus.SHADOW_REJECT,
    }
    return mapping.get(raw)


def decide_evolution(
    sources: dict[str, Any],
    *,
    ignore_running_shadow: bool = False,
) -> EvolutionResult:
    if not ignore_running_shadow:
        shadow = sources.get("shadow_experiment")
        if shadow:
            shadow_status = _shadow_status_from_state(shadow)
            if shadow_status is not None:
                candidate = shadow.get("candidate") or sources.get("strategy_review", {}).get(
                    "final_verdict"
                )
                evidence = _evidence_block(sources, candidate if isinstance(candidate, dict) else None)
                if shadow.get("metrics"):
                    evidence.update({k: v for k, v in shadow["metrics"].items() if v is not None})
                return {
                    "version": EVOLUTION_VERSION,
                    "status": shadow_status.value,
                    "reason": shadow.get("reason", "Shadow phase active"),
                    "next_review_trades": shadow.get("next_review_trades"),
                    "candidate": shadow.get("candidate"),
                    "evidence": evidence,
                    "watch_reasons": [],
                    "sources_meta": _sources_meta(sources),
                    "shadow": shadow,
                }

    review = sources.get("strategy_review", {})
    verdict = review.get("final_verdict", {})
    live = sources.get("live_sample", {})
    scientist = sources.get("scientist", {})
    brain = sources.get("trading_brain", {})
    ai = sources.get("ai_agent", {})

    trades_since = int(live.get("current_since_change", 0))
    next_review = max(0, MIN_TRADES_SINCE_CHANGE - trades_since)
    candidate = _extract_candidate(sources)

    scientist_step = scientist.get("best_next_step") or {}
    scientist_blocked = bool(scientist_step.get("blocked", True))
    overfit = _overfit_level(sources)
    wf_label, wf_ok = _walk_forward_label(sources.get("optimizer", {}).get("walk_forward"))
    safety_blocked = bool(verdict.get("safety_blocked", True))
    confidence = float(verdict.get("confidence_pct", 0))
    brain_support = bool(brain.get("top_knowledge"))

    ai_decisions = ai.get("decisions") or {}
    ai_allow = int(ai_decisions.get("ALLOW", 0))
    ai_skip = int(ai_decisions.get("SKIP", 0))
    ai_supportive = ai_allow >= ai_skip

    watch_reasons: list[str] = []

    if trades_since < MIN_TRADES_SINCE_CHANGE:
        reason = verdict.get("reason") or (
            f"Недостаточно доказательств — {trades_since} сделок после последнего изменения "
            f"(нужно {MIN_TRADES_SINCE_CHANGE})."
        )
        return {
            "version": EVOLUTION_VERSION,
            "status": EvolutionStatus.KEEP.value,
            "reason": reason,
            "next_review_trades": next_review,
            "candidate": candidate,
            "evidence": _evidence_block(sources, candidate),
            "watch_reasons": watch_reasons,
            "sources_meta": _sources_meta(sources),
        }

    if not candidate:
        return {
            "version": EVOLUTION_VERSION,
            "status": EvolutionStatus.KEEP.value,
            "reason": verdict.get("reason")
            or "Недостаточно доказательств — нет кандидата на изменение.",
            "next_review_trades": next_review,
            "candidate": None,
            "evidence": _evidence_block(sources, None),
            "watch_reasons": watch_reasons,
            "sources_meta": _sources_meta(sources),
        }

    if scientist_blocked:
        watch_reasons.append("Scientist: no validated experiment")
    if not wf_ok:
        watch_reasons.append(f"Walk-forward {wf_label}")
    if overfit == "HIGH":
        watch_reasons.append("Overfit HIGH")
    if safety_blocked:
        watch_reasons.append("Strategy Review safety gate blocked")
    if confidence < MIN_CONFIDENCE_PCT:
        watch_reasons.append(f"Confidence {confidence:.0f}% < {MIN_CONFIDENCE_PCT:.0f}%")
    if not brain_support:
        watch_reasons.append("Trading Brain: weak causal signal")
    if not ai_supportive:
        watch_reasons.append("AI Agent: SKIP outweighs ALLOW")

    ready = (
        not safety_blocked
        and not scientist_blocked
        and wf_ok
        and overfit in ("LOW", "MEDIUM")
        and confidence >= MIN_CONFIDENCE_PCT
    )

    if ready:
        return {
            "version": EVOLUTION_VERSION,
            "status": EvolutionStatus.READY_FOR_SHADOW.value,
            "reason": (
                "Все gates пройдены — создаётся shadow-эксперимент. "
                "Торговая логика не меняется."
            ),
            "next_review_trades": None,
            "candidate": candidate,
            "evidence": _evidence_block(sources, candidate),
            "watch_reasons": watch_reasons,
            "sources_meta": _sources_meta(sources),
        }

    if watch_reasons:
        return {
            "version": EVOLUTION_VERSION,
            "status": EvolutionStatus.WATCH.value,
            "reason": "Сигнал есть, но доказательства недостаточны: " + "; ".join(watch_reasons),
            "next_review_trades": next_review,
            "candidate": candidate,
            "evidence": _evidence_block(sources, candidate),
            "watch_reasons": watch_reasons,
            "sources_meta": _sources_meta(sources),
        }

    return {
        "version": EVOLUTION_VERSION,
        "status": EvolutionStatus.KEEP.value,
        "reason": verdict.get("reason") or "KEEP CURRENT SETTINGS",
        "next_review_trades": next_review,
        "candidate": candidate,
        "evidence": _evidence_block(sources, candidate),
        "watch_reasons": watch_reasons,
        "sources_meta": _sources_meta(sources),
    }


def _evidence_block(
    sources: dict[str, Any],
    candidate: EvolutionCandidate | None,
) -> dict[str, Any]:
    live = sources.get("live_sample", {})
    opt = sources.get("optimizer", {}).get("parameter_optimizer", {})
    wf_label, _ = _walk_forward_label(sources.get("optimizer", {}).get("walk_forward"))
    return {
        "trades": int(live.get("total_trades", 0)),
        "trades_since_change": int(live.get("current_since_change", 0)),
        "expected_pf_pct": float(
            (candidate or {}).get("expected_pf_pct", opt.get("expected_improvement_pct", 0))
        ),
        "expected_dd_pct": (candidate or {}).get("expected_dd_pct"),
        "walk_forward": (candidate or {}).get("walk_forward", wf_label),
        "overfit": (candidate or {}).get("overfit", _overfit_level(sources)),
    }


def _sources_meta(sources: dict[str, Any]) -> dict[str, Any]:
    opt_meta = (sources.get("optimizer") or {}).get("meta", {})
    review = sources.get("strategy_review", {})
    return {
        "optimizer_cached_at": opt_meta.get("cached_at") or opt_meta.get("generated_at"),
        "strategy_review_cached_at": review.get("cached_at"),
        "scientist_experiments": len(sources.get("scientist", {}).get("top_experiments", [])),
        "brain_contexts": sources.get("trading_brain", {}).get("contexts_stored", 0),
        "ai_signals": sources.get("ai_agent", {}).get("meta", {}).get("signals_recorded", 0),
    }
