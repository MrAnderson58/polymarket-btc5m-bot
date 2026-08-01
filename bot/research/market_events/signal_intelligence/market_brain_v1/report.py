"""Reports for Adaptive Market Brain V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "market_brain_v1"
REPORT_MD = BASE_DIR / "MARKET_BRAIN_REPORT.md"
EXPLAIN_MD = BASE_DIR / "BRAIN_EXPLANATIONS.md"
CONFLICTS_MD = BASE_DIR / "MODULE_CONFLICTS.md"


def format_brain_report(result: dict[str, Any]) -> str:
    perf = result.get("replay_performance") or {}
    lines = [
        "# MARKET_BRAIN_REPORT",
        "",
        "_Adaptive Market Brain V1 — unified research opinion layer. "
        "Does not modify Paper / Execution / Strategy / Gate._",
        "",
        "## Run",
        "",
        f"- trades analyzed: **{result.get('n_rows')}**",
        f"- decisions: **{result.get('n_decisions')}**",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- NO_TRADE (conflicts): **{result.get('n_no_trade')}**",
        f"- lake source: `{(result.get('load_stats') or {}).get('source')}`",
        "",
        "## Module weights",
        "",
    ]
    for k, v in (result.get("module_weights") or {}).items():
        lines.append(f"- `{k}`: {v}")
    lines.extend([
        "",
        "## Agreement matrix (mean pairwise)",
        "",
        f"```json\n{json.dumps(result.get('agreement_summary') or {}, indent=2)}\n```",
        "",
        "## Conflict statistics",
        "",
        f"```json\n{json.dumps(result.get('conflict_stats') or {}, indent=2)}\n```",
        "",
        "## Calibrated confidence",
        "",
        f"- mean raw: {result.get('mean_confidence_raw')}",
        f"- mean calibrated: {result.get('mean_confidence_calibrated')}",
        f"- calibration bins: {len(result.get('calibration_table') or [])}",
        "",
        "## Historical replay performance (Brain vs modules)",
        "",
        f"```json\n{json.dumps(perf, indent=2, default=str)}\n```",
        "",
        "## Integrity",
        "",
        f"- gate_unchanged: {result.get('gate_unchanged')}",
        f"- strategy_unchanged: {result.get('strategy_unchanged')}",
        f"- paper_unchanged: {result.get('paper_unchanged')}",
        f"- execution_unchanged: {result.get('execution_unchanged')}",
        f"- research_only: {result.get('research_only')}",
        "",
    ])
    return "\n".join(lines)


def format_explanations_md(result: dict[str, Any]) -> str:
    lines = ["# BRAIN_EXPLANATIONS", "", "Example fused decisions with reasons.", ""]
    for ex in (result.get("explanation_examples") or [])[:10]:
        lines.append(f"## trade_id={ex.get('trade_id')} → {ex.get('decision')}")
        lines.append("")
        lines.append(f"- confidence_calibrated: {ex.get('confidence_calibrated')}")
        lines.append(f"- P(buy): {ex.get('probability_buy')} EV={ex.get('expected_ev')} risk={ex.get('risk')}")
        lines.append("")
        lines.append("```")
        lines.append(str((ex.get("explanation") or {}).get("text") or ""))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def format_conflicts_md(result: dict[str, Any]) -> str:
    lines = [
        "# MODULE_CONFLICTS",
        "",
        f"Conflict rate: **{(result.get('conflict_stats') or {}).get('conflict_rate')}**",
        "",
        "| trade_id | level | score | strong modules |",
        "|---|---|---|---|",
    ]
    for c in (result.get("conflict_examples") or [])[:40]:
        strong = ", ".join(
            f"{m.get('module')}={m.get('direction')}"
            for m in (c.get("strong_modules") or [])[:6]
        )
        lines.append(
            f"| {c.get('trade_id')} | {c.get('level')} | {c.get('conflict_score')} | {strong} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    report = format_brain_report(result)
    REPORT_MD.write_text(report, encoding="utf-8")
    (OUT_DIR / "MARKET_BRAIN_REPORT.md").write_text(report, encoding="utf-8")
    paths["report_md"] = str(REPORT_MD)

    expl = format_explanations_md(result)
    EXPLAIN_MD.write_text(expl, encoding="utf-8")
    (OUT_DIR / "BRAIN_EXPLANATIONS.md").write_text(expl, encoding="utf-8")
    paths["explanations_md"] = str(EXPLAIN_MD)

    conf = format_conflicts_md(result)
    CONFLICTS_MD.write_text(conf, encoding="utf-8")
    (OUT_DIR / "MODULE_CONFLICTS.md").write_text(conf, encoding="utf-8")
    paths["conflicts_md"] = str(CONFLICTS_MD)

    payloads = {
        "module_weights.json": result.get("module_weights") or {},
        "agreement_summary.json": result.get("agreement_summary") or {},
        "conflict_stats.json": result.get("conflict_stats") or {},
        "calibration_table.json": result.get("calibration_table") or [],
        "replay_performance.json": result.get("replay_performance") or {},
        "explanation_examples.json": result.get("explanation_examples") or [],
        "summary.json": {
            "n_rows": result.get("n_rows"),
            "n_decisions": result.get("n_decisions"),
            "n_no_trade": result.get("n_no_trade"),
            "elapsed_sec": result.get("elapsed_sec"),
            "mean_confidence_calibrated": result.get("mean_confidence_calibrated"),
        },
    }
    for name, payload in payloads.items():
        p = OUT_DIR / name
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[name] = str(p)
    return paths


__all__ = [
    "OUT_DIR",
    "format_brain_report",
    "format_conflicts_md",
    "format_explanations_md",
    "write_artifacts",
]
