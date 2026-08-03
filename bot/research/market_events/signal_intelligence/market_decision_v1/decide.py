"""Fuse existing module scores into one TRADE / NO TRADE decision."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.market_decision_v1 import thresholds as T
from bot.research.market_events.signal_intelligence.market_decision_v1.sources import (
    hist_blend,
    score_brain,
    score_causality,
    score_dna,
    score_edge,
    score_fingerprint,
    score_replay,
    score_rules,
    score_timeline,
)


def _vote_direction(scores: dict[str, dict[str, Any]]) -> str | None:
    votes: list[str] = []
    for name in ("brain", "fingerprint", "timeline", "edge", "replay", "causality", "dna"):
        s = scores.get(name) or {}
        d = s.get("direction")
        if d in ("LONG", "SHORT") and (s.get("pass") or name == "brain"):
            # brain direction always counts if BUY/SELL
            if name == "brain" and not s.get("pass"):
                continue
            votes.append(d)
    if not votes:
        # softer: any directional hint
        for s in scores.values():
            d = s.get("direction")
            if d in ("LONG", "SHORT"):
                votes.append(d)
    if not votes:
        return None
    long_n = votes.count("LONG")
    short_n = votes.count("SHORT")
    if long_n == short_n:
        return None
    return "LONG" if long_n > short_n else "SHORT"


def decide_one(ctx: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
    """Single research decision using only prebuilt engines."""
    tid = int(trade.get("trade_id") or trade.get("id") or 0)
    fp = score_fingerprint(ctx, tid)
    tl = score_timeline(ctx, tid)
    dna = score_dna(ctx, tid)
    rules = score_rules(ctx, tid)
    edge = score_edge(ctx, trade)
    replay = score_replay(ctx, tid, trade)
    causal = score_causality(ctx, tid, trade)
    brain = score_brain(ctx, trade)

    scores = {
        "fingerprint": fp,
        "timeline": tl,
        "dna": dna,
        "rules": rules,
        "edge": edge,
        "replay": replay,
        "causality": causal,
        "brain": brain,
    }
    hist = hist_blend([fp, tl, dna, rules, edge])

    reasons: list[str] = []
    supporting = 0
    for name, s in scores.items():
        if s.get("pass"):
            supporting += 1
            if s.get("reason"):
                reasons.append(str(s["reason"]))

    # Confidence: mix of module passes + brain conviction (not raw fp euclidean %).
    conf_parts: list[float] = []
    for name, s in scores.items():
        if name == "brain" and s.get("confidence") is not None:
            conf_parts.append(float(s["confidence"]))
        elif s.get("pass"):
            conf_parts.append(0.78)
        elif s.get("ok"):
            conf_parts.append(0.32)
    confidence = round(sum(conf_parts) / len(conf_parts), 4) if conf_parts else 0.0

    direction = _vote_direction(scores)
    no_reasons: list[str] = []

    # Hard vetoes
    if rules.get("blocked"):
        no_reasons.append(str(rules.get("reason") or "Rule blocked"))
    if str(brain.get("brain_decision") or "") == "NO_TRADE":
        no_reasons.append("Brain NO_TRADE")
    if str(brain.get("conflict") or "") == "HIGH":
        no_reasons.append("Brain conflict HIGH")

    # Edge / history
    hist_ok = (
        (hist.get("historical_wr") is not None and float(hist["historical_wr"]) >= T.MIN_HIST_WR)
        and (hist.get("historical_ev") is not None and float(hist["historical_ev"]) >= T.MIN_HIST_EV)
    )
    if not hist_ok:
        no_reasons.append("No historical edge")

    fp_or_tl = bool(fp.get("pass") or tl.get("pass"))
    if not fp_or_tl:
        if not fp.get("pass"):
            no_reasons.append(str(fp.get("reason") or "Fingerprint mismatch"))
        if not tl.get("pass"):
            no_reasons.append(str(tl.get("reason") or "Timeline mismatch"))

    structure_ok = bool(dna.get("pass") or rules.get("pass") or edge.get("pass"))
    if not structure_ok:
        no_reasons.append("No DNA/Rules/Edge support")

    if T.REQUIRE_REPLAY_OR_CAUSAL and int(ctx.get("n_replays") or 0) >= T.MIN_REPLAY_LIBRARY:
        if not (replay.get("pass") or causal.get("pass")):
            if not replay.get("ok"):
                no_reasons.append("Replay unknown")
            elif not causal.get("ok"):
                no_reasons.append("Causality unknown")
            else:
                no_reasons.append("Replay/Causality weak")

    if supporting < T.MIN_SUPPORTING_MODULES:
        no_reasons.append(f"Supporting modules {supporting}<{T.MIN_SUPPORTING_MODULES}")

    if confidence < T.MIN_CONFIDENCE:
        no_reasons.append(f"Confidence {round(confidence * 100)}%")

    if direction is None:
        no_reasons.append("Direction unclear")

    # Deduplicate reasons preserving order
    seen: set[str] = set()
    uniq_no: list[str] = []
    for r in no_reasons:
        if r not in seen:
            seen.add(r)
            uniq_no.append(r)

    trade_ok = len(uniq_no) == 0 and direction in ("LONG", "SHORT")
    decision = "TRADE" if trade_ok else "NO TRADE"
    why = reasons if trade_ok else uniq_no

    # Prefer explicit WHY lines in user format
    why_lines: list[str] = []
    if trade_ok:
        if fp.get("similarity_pct") is not None:
            why_lines.append(f"Fingerprint {fp.get('similarity_pct')}%")
        if tl.get("similarity_pct") is not None:
            why_lines.append(f"Timeline {tl.get('similarity_pct')}%")
        if dna.get("similarity_pct") is not None and dna.get("pass"):
            why_lines.append(f"DNA {dna.get('similarity_pct')}%")
        if replay.get("similarity_pct") is not None and replay.get("pass"):
            why_lines.append(f"Replay {replay.get('similarity_pct')}%")
        if rules.get("pass") and rules.get("rule"):
            why_lines.append(f"{rules.get('rule')} matched")
        elif edge.get("pass"):
            why_lines.append("Edge matched")
        if hist.get("historical_wr") is not None:
            why_lines.append(f"Historical WR {hist.get('historical_wr')}%")
        if hist.get("historical_pf") is not None:
            why_lines.append(f"Historical PF {hist.get('historical_pf')}")
        if hist.get("historical_ev") is not None:
            why_lines.append(f"Historical EV {hist.get('historical_ev')}%")
        why_lines.append(f"Confidence {round(confidence * 100)}%")
    else:
        why_lines = [r for r in uniq_no if not str(r).startswith("Confidence ")]
        why_lines.append(f"Confidence {round(confidence * 100)}%")

    return {
        "ok": True,
        "trade_id": tid,
        "symbol": trade.get("symbol"),
        "decision": decision,
        "direction": direction if trade_ok else None,
        "confidence": confidence,
        "confidence_pct": round(confidence * 100.0, 2),
        "why": why_lines,
        "reasons": why_lines,
        "historical_wr": hist.get("historical_wr"),
        "historical_pf": hist.get("historical_pf"),
        "historical_ev": hist.get("historical_ev"),
        "scores": {
            k: {kk: vv for kk, vv in v.items() if kk not in ("opinion", "opinions", "fusion", "explanation", "pred")}
            for k, v in scores.items()
        },
        "supporting_modules": supporting,
        "research_only": True,
        "recommendation": "RESEARCH ONLY",
    }


def format_decision(result: dict[str, Any]) -> str:
    lines = ["Decision", ""]
    if result.get("decision") == "TRADE":
        for w in result.get("why") or []:
            lines.append(w)
        lines.extend(["", "Decision", f"  TRADE {result.get('direction')}"])
    else:
        lines.append("NO TRADE")
        lines.append("")
        lines.append("Reasons")
        for w in result.get("why") or []:
            lines.append(f"  {w}")
    lines.extend(["", "Recommendation", f"  {result.get('recommendation') or 'RESEARCH ONLY'}"])
    return "\n".join(lines)


__all__ = ["decide_one", "format_decision"]
