"""Render evolution status for daily CLI and report §46."""

from __future__ import annotations

from typing import Any

from bot.evolution.state import EvolutionStatus


_STATUS_LABELS = {
    EvolutionStatus.KEEP.value: "KEEP",
    EvolutionStatus.WATCH.value: "WATCH",
    EvolutionStatus.READY_FOR_SHADOW.value: "READY FOR SHADOW",
    EvolutionStatus.SHADOW_RUNNING.value: "SHADOW RUNNING",
    EvolutionStatus.SHADOW_PROMOTE.value: "SHADOW PROMOTE",
    EvolutionStatus.SHADOW_REJECT.value: "SHADOW REJECT",
}


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("_", " "))


def _format_candidate(candidate: dict[str, Any]) -> str:
    param = candidate.get("parameter", "entry")
    from_v = candidate.get("from_value")
    to_v = candidate.get("to_value")
    if param == "entry" and from_v is not None and to_v is not None:
        return f"Entry {float(from_v):.2f} → {float(to_v):.2f}"
    if from_v is not None and to_v is not None:
        return f"{param} {from_v} → {to_v}"
    return str(candidate.get("label", param))


def render_evolution_block(evolution: dict[str, Any]) -> str:
    status = evolution.get("status", EvolutionStatus.KEEP.value)
    lines = [
        "========================",
        "",
        "EVOLUTION STATUS",
        "",
        _status_label(status),
        "",
    ]

    if status == EvolutionStatus.READY_FOR_SHADOW.value:
        cand = evolution.get("candidate") or {}
        evidence = evolution.get("evidence") or {}
        lines.extend(
            [
                "Candidate:",
                "",
                _format_candidate(cand),
                "",
                "Confidence:",
                "",
                f"{cand.get('confidence_pct', 0):.0f}%",
                "",
                "Evidence:",
                "",
                f"{evidence.get('trades', cand.get('evidence_trades', 0))} trades",
                "",
            ]
        )
        pf = evidence.get("expected_pf_pct", cand.get("expected_pf_pct"))
        if pf is not None:
            lines.extend(["Expected PF:", "", f"+{float(pf):.0f}%", ""])
        dd = evidence.get("expected_dd_pct")
        if dd is not None:
            lines.extend(["Expected DD:", "", f"{float(dd):+.0f}%", ""])
        lines.extend(
            [
                "Walk Forward:",
                "",
                str(evidence.get("walk_forward", cand.get("walk_forward", "?"))),
                "",
                "Overfit:",
                "",
                str(evidence.get("overfit", cand.get("overfit", "?"))),
                "",
            ]
        )
        if evolution.get("reason"):
            lines.extend(["Note:", "", evolution["reason"], ""])
    else:
        if evolution.get("reason"):
            lines.extend(["Reason:", "", evolution["reason"], ""])
        next_review = evolution.get("next_review_trades")
        if next_review is not None:
            lines.extend(["Next review:", "", f"{next_review} trades", ""])
        if status == EvolutionStatus.WATCH.value and evolution.get("watch_reasons"):
            lines.extend(["Watch:", "", "; ".join(evolution["watch_reasons"]), ""])

    lines.extend(["========================", ""])
    return "\n".join(lines)


def render_evolution_section(evolution: dict[str, Any]) -> list[str]:
    lines = [
        "",
        f"**Status:** {_status_label(evolution.get('status', EvolutionStatus.KEEP.value))}",
        "",
    ]
    if evolution.get("reason"):
        lines.append(f"**Reason:** {evolution['reason']}")
    if evolution.get("next_review_trades") is not None:
        lines.append(f"**Next review:** {evolution['next_review_trades']} trades")

    cand = evolution.get("candidate")
    if cand:
        lines.append(f"**Candidate:** {_format_candidate(cand)}")
        lines.append(f"**Confidence:** {cand.get('confidence_pct', 0):.0f}%")

    evidence = evolution.get("evidence") or {}
    if evidence:
        lines.append(f"**Evidence:** {evidence.get('trades', 0)} trades")
        if evidence.get("expected_pf_pct") is not None:
            lines.append(f"**Expected PF:** +{evidence['expected_pf_pct']:.0f}%")
        if evidence.get("expected_dd_pct") is not None:
            lines.append(f"**Expected DD:** {evidence['expected_dd_pct']:+.0f}%")
        lines.append(f"**Walk Forward:** {evidence.get('walk_forward', '?')}")
        lines.append(f"**Overfit:** {evidence.get('overfit', '?')}")

    if evolution.get("watch_reasons"):
        lines.append("**Watch reasons:**")
        for reason in evolution["watch_reasons"]:
            lines.append(f"- {reason}")

    meta = evolution.get("sources_meta") or {}
    if meta:
        lines.append("")
        lines.append("_Sources (read-only):_")
        for key, val in meta.items():
            lines.append(f"- {key}: {val}")

    lines.append("")
    lines.append(
        "_Phase 1: observe-only. No shadow experiments, no strategy changes._"
    )
    return lines
