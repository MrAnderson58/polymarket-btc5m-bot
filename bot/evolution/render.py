"""Render evolution status for daily CLI and report §46–§49."""

from __future__ import annotations

from typing import Any

from bot.evolution.constants import SHADOW_TARGET_SAMPLE
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
    if param in ("entry", "entry_threshold") and from_v is not None and to_v is not None:
        return f"Entry {float(from_v):.2f} → {float(to_v):.2f}"
    if from_v is not None and to_v is not None:
        return f"{param} {from_v} → {to_v}"
    return str(candidate.get("label", param))


def _format_parameter_label(parameter: str) -> str:
    if parameter in ("entry", "entry_threshold"):
        return "Entry"
    return parameter.replace("_", " ").title()


def _separator() -> list[str]:
    return ["----------------------------------", ""]


def render_evolution_block(evolution: dict[str, Any]) -> str:
    status = evolution.get("status", EvolutionStatus.KEEP.value)
    shadow = evolution.get("shadow") or {}
    lines = _separator() + [
        "EVOLUTION STATUS",
        "",
        _status_label(status),
        "",
    ]

    if status in (
        EvolutionStatus.SHADOW_PROMOTE.value,
        EvolutionStatus.SHADOW_REJECT.value,
    ):
        lines.extend(["SHADOW COMPLETE", ""])
        verdict = shadow.get("metrics", {}).get("verdict") or (
            "PROMOTE" if status == EvolutionStatus.SHADOW_PROMOTE.value else "REJECT"
        )
        lines.extend([verdict, ""])
        metrics = shadow.get("metrics") or {}
        if metrics:
            lines.extend(
                [
                    f"Live PF {metrics.get('live_pf', '?')} | Shadow PF {metrics.get('shadow_pf', '?')}",
                    f"Live WR {metrics.get('live_wr', '?')}% | Shadow WR {metrics.get('shadow_wr', '?')}%",
                    f"Live DD {metrics.get('live_dd', '?')} | Shadow DD {metrics.get('shadow_dd', '?')}",
                    "",
                ]
            )
    elif status == EvolutionStatus.SHADOW_RUNNING.value:
        cand = shadow.get("candidate") or evolution.get("candidate") or {}
        target = int(shadow.get("target_sample_size") or SHADOW_TARGET_SAMPLE)
        sample = int(shadow.get("sample_size") or 0)
        evidence = evolution.get("evidence") or {}
        lines.extend(
            [
                "Experiment",
                "",
                _format_parameter_label(str(cand.get("parameter", "entry_threshold"))),
                "",
                _format_candidate(cand).replace("Entry ", ""),
                "",
                "Progress",
                "",
                f"{sample} / {target}",
                "",
            ]
        )
        pf = evidence.get("expected_pf_pct")
        if pf is not None:
            lines.extend(["Expected PF", "", f"+{float(pf):.0f}%", ""])
    elif status == EvolutionStatus.READY_FOR_SHADOW.value:
        cand = evolution.get("candidate") or {}
        evidence = evolution.get("evidence") or {}
        lines.extend(
            [
                "Experiment",
                "",
                _format_parameter_label(str(cand.get("parameter", "entry"))),
                "",
                _format_candidate(cand).replace("Entry ", ""),
                "",
                "Progress",
                "",
                f"0 / {SHADOW_TARGET_SAMPLE}",
                "",
            ]
        )
        pf = evidence.get("expected_pf_pct", cand.get("expected_pf_pct"))
        if pf is not None:
            lines.extend(["Expected PF", "", f"+{float(pf):.0f}%", ""])
        if evolution.get("reason"):
            lines.extend(["Note", "", evolution["reason"], ""])
    else:
        if evolution.get("reason"):
            lines.extend(["Reason", "", evolution["reason"], ""])
        next_review = evolution.get("next_review_trades")
        if next_review is not None:
            lines.extend(["Next review", "", f"{next_review} trades", ""])
        if status == EvolutionStatus.WATCH.value and evolution.get("watch_reasons"):
            lines.extend(["Watch", "", "; ".join(evolution["watch_reasons"]), ""])

    lines.extend(_separator())
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
    lines.append("_Observe-only. Shadow does not change live/paper execution._")
    return lines


def render_shadow_evolution_section(evolution: dict[str, Any]) -> list[str]:
    shadow_report = evolution.get("shadow_report") or {}
    shadow = evolution.get("shadow") or {}
    exp = shadow_report.get("experiment") or shadow
    if not exp:
        return ["", "_No shadow experiment yet._", ""]

    lines = [
        "",
        f"**Parameter:** {exp.get('parameter', '?')}",
        f"**Current → Shadow:** {exp.get('current_value')} → {exp.get('shadow_value')}",
        f"**Status:** {exp.get('status', shadow.get('status', '?'))}",
    ]
    if exp.get("status") == "RUNNING" or shadow.get("status") == "RUNNING":
        target = int(exp.get("target_sample_size") or shadow.get("target_sample_size") or SHADOW_TARGET_SAMPLE)
        sample = int(exp.get("sample_size") or shadow.get("sample_size") or 0)
        lines.append(f"**Progress:** {sample} / {target} evaluable trades")
    decisions = shadow_report.get("decisions") or {}
    if decisions:
        lines.append(
            f"**Shadow decisions:** WOULD_ENTER {decisions.get('WOULD_ENTER', 0)} | "
            f"WOULD_SKIP {decisions.get('WOULD_SKIP', 0)}"
        )
    metrics = shadow.get("metrics") or {}
    if metrics.get("verdict"):
        lines.extend(
            [
                "",
                f"**Verdict:** {metrics['verdict']}",
                f"**Live PF / Shadow PF:** {metrics.get('live_pf')} / {metrics.get('shadow_pf')}",
                f"**Live WR / Shadow WR:** {metrics.get('live_wr')}% / {metrics.get('shadow_wr')}%",
                f"**Live DD / Shadow DD:** {metrics.get('live_dd')} / {metrics.get('shadow_dd')}",
            ]
        )
    lines.append("")
    lines.append("_Counterfactual only — main strategy unchanged._")
    return lines


# ---------------------------------------------------------------------------
# Decision Council rendering
# ---------------------------------------------------------------------------

def render_council_block(evolution: dict[str, Any]) -> str:
    """Render Decision Council block for daily CLI output."""
    council = evolution.get("council")
    if not council:
        return ""

    lines = _separator() + [
        "DECISION COUNCIL",
        "",
    ]

    votes = council.get("votes", [])
    max_source_len = max((len(v["source"]) for v in votes), default=10)

    for v in votes:
        source_pad = v["source"].ljust(max_source_len)
        lines.append(f"  {source_pad}  {v['label']}")

    lines.append("")
    lines.append(f"  {'Final'.ljust(max_source_len)}  {council.get('final_label', 'KEEP')}")
    lines.append("")

    confidence = council.get("confidence_pct", 0)
    if confidence > 0:
        lines.append(f"  Confidence  {confidence:.0f}%")
        lines.append("")

    reason = council.get("reason", "")
    if reason:
        lines.append(f"  Reason")
        lines.append(f"  {reason}")
        lines.append("")

    status = council.get("status", EvolutionStatus.KEEP.value)
    lines.append(f"  Decision: {_status_label(status)}")
    lines.append("")
    lines.extend(_separator())
    return "\n".join(lines)


def render_council_section(evolution: dict[str, Any]) -> list[str]:
    """Render Decision Council section for the markdown report (§49)."""
    council = evolution.get("council")
    if not council:
        return ["", "_Decision Council not available._", ""]

    lines = [""]
    votes = council.get("votes", [])

    lines.append("| Source | Vote | Parameter | Value |")
    lines.append("|--------|------|-----------|-------|")
    for v in votes:
        param_str = v.get("parameter") or "—"
        value_str = f"{v['value']:.4g}" if v.get("value") is not None else "—"
        lines.append(f"| {v['source']} | {v['label']} | {param_str} | {value_str} |")

    lines.append("")
    lines.append(f"**Final Decision:** {council.get('final_label', 'KEEP')}")
    lines.append(f"**Status:** {_status_label(council.get('status', 'KEEP'))}")

    confidence = council.get("confidence_pct", 0)
    if confidence > 0:
        lines.append(f"**Confidence:** {confidence:.0f}%")

    reason = council.get("reason", "")
    if reason:
        lines.append(f"**Reason:** {reason}")

    pf = council.get("expected_pf_pct", 0)
    if pf:
        lines.append(f"**Expected PF improvement:** +{pf:.0f}%")

    lines.append("")
    lines.append("_All recommendations go through Council. No source decides alone._")
    return lines
