"""Render Scientist research reports (markdown + JSON)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR


def render_scientist_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# AI Scientist Research Report",
        "",
        f"_Version {report.get('version', '?')} | observe-only — no trading impact_",
        "",
        "## NEW HYPOTHESES",
        "",
    ]
    hypotheses = report.get("new_hypotheses", [])
    if hypotheses:
        for h in hypotheses:
            lines.append(f"- **{h['hypothesis_type']}**: {h['description']}")
            lines.append(
                f"  - confidence {h['confidence']:.0f}% | "
                f"expected +{h.get('expected_improvement', 0):.1f}% | n={h['sample_n']}"
            )
    else:
        lines.append("_No new hypotheses this run (duplicates filtered or insufficient data)._")

    lines += ["", "## TOP EXPERIMENTS", ""]
    for i, exp in enumerate(report.get("top_experiments", [])[:10], 1):
        lines.append(
            f"{i}. **{exp.get('title', '?')}** [{exp.get('status')}] "
            f"priority {exp.get('priority')} | risk {exp.get('risk_level')}"
        )
        lines.append(f"   - {exp.get('description', '')[:120]}")
        lines.append(
            f"   - confidence {float(exp.get('confidence', 0)):.0f}% | "
            f"score {float(exp.get('ranking_score', 0)):.1f}"
        )

    lines += ["", "## FAILED IDEAS", ""]
    for exp in report.get("failed_ideas", [])[:10]:
        checks = (exp.get("validation") or {}).get("checks", {})
        reason_parts = []
        if not checks.get("replay", {}).get("passed"):
            reason_parts.append("replay failed")
        if not checks.get("walk_forward", {}).get("passed"):
            reason_parts.append("walk-forward failed")
        if checks.get("overfit", {}).get("risk") == "HIGH":
            reason_parts.append("high overfit")
        if not checks.get("sample_size", {}).get("passed"):
            reason_parts.append("sample too small")
        lines.append(f"- {exp.get('title')}: {', '.join(reason_parts) or exp.get('status')}")

    summary = report.get("summary", {})
    lines += [
        "",
        "## RESEARCH SUMMARY",
        "",
        f"- Patterns found: {summary.get('patterns_found', 0)}",
        f"- New hypotheses: {summary.get('new_hypotheses', 0)}",
        f"- Passed validation: {summary.get('passed', 0)}",
        f"- Failed/rejected: {summary.get('failed', 0)}",
        f"- Total experiments in queue: {summary.get('total_experiments', 0)}",
        "",
        "## BEST NEXT STEP",
        "",
    ]
    best = report.get("best_next_step", {})
    if best.get("blocked"):
        lines.append(f"**No recommendation** — {best.get('reason', '')}")
    else:
        lines.append(f"**Recommendation:** {best.get('recommendation')}")
        lines.append(f"- Expected PF: **+{best.get('expected_pf_pct', 0):.0f}%**")
        lines.append(f"- Confidence: **{best.get('confidence_pct', 0):.0f}%**")
        lines.append(f"- Risk: **{best.get('risk')}** | Priority: **{best.get('priority')}**")
        if best.get("reasons"):
            lines.append("- " + " | ".join(best["reasons"]))

    lines += [
        "",
        "---",
        "",
        "**Quality gate:** recommendations require ≥300 trades, walk-forward pass, "
        "and no high overfit risk.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_scientist_report(report: dict[str, Any], *, out_dir: Path | None = None) -> tuple[Path, Path]:
    if out_dir is None:
        out_dir = BASE_DIR / "scientist_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = out_dir / f"scientist_{stamp}.md"
    json_path = out_dir / f"scientist_{stamp}.json"
    md_path.write_text(render_scientist_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return md_path, json_path
