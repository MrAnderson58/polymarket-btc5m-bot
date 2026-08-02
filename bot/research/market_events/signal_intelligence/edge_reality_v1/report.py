"""Reports for Edge Reality Audit V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "edge_reality_v1"
EDGE_REALITY_MD = BASE_DIR / "EDGE_REALITY_REPORT.md"
ATTRIBUTION_MD = BASE_DIR / "MODULE_ATTRIBUTION.md"
REDUNDANCY_MD = BASE_DIR / "MODULE_REDUNDANCY.md"
PARETO_MD = BASE_DIR / "PARETO_REPORT.md"


def _tbl_attribution(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| module | ΔEV | ΔPF | ΔWR | MI | IG | Prec | Rec | F1 | grade |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        if r.get("module") == "production":
            grade = "BASE"
        else:
            grade = r.get("grade") or ""
        vs = r.get("vs_production") or {}
        lines.append(
            f"| `{r.get('module')}` | "
            f"{r.get('delta_ev') if r.get('delta_ev') is not None else vs.get('delta_ev')} | "
            f"{r.get('delta_pf') if r.get('delta_pf') is not None else vs.get('delta_pf')} | "
            f"{r.get('delta_wr') if r.get('delta_wr') is not None else vs.get('delta_wr')} | "
            f"{r.get('mutual_information')} | {r.get('information_gain')} | "
            f"{r.get('precision')} | {r.get('recall')} | {r.get('f1')} | {grade} |"
        )
    return lines


def format_edge_reality_report(result: dict[str, Any]) -> str:
    simp = result.get("simplification") or {}
    pareto = result.get("pareto") or {}
    lines = [
        "# EDGE_REALITY_REPORT",
        "",
        "_Edge Reality Audit V1 — research-only module attribution. "
        "Does not modify Paper / Execution / Strategy / Gate / Optimizer / Brain._",
        "",
        "## Run",
        "",
        f"- closed S42 trades: **{result.get('n_trades')}**",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- source: `{(result.get('load_stats') or {}).get('source')}`",
        f"- run_id: `{result.get('run_id')}`",
        "",
        "## Module attribution (vs production)",
        "",
    ]
    lines.extend(_tbl_attribution(result.get("attribution") or []))
    lines.extend([
        "",
        "## Incremental value",
        "",
        "| step | added | ΔEV vs prev | ΔEV vs prod | EV |",
        "|---:|---|---:|---:|---:|",
    ])
    for s in result.get("incremental") or []:
        dprev = (s.get("delta_vs_prev") or {})
        dprod = (s.get("delta_vs_production") or {})
        m = s.get("metrics") or {}
        lines.append(
            f"| {s.get('step')} | `{s.get('added')}` | {dprev.get('delta_ev')} | "
            f"{dprod.get('delta_ev')} | {m.get('ev')} |"
        )
    lines.extend([
        "",
        "## Remove-one (ensemble hurt)",
        "",
        "| removed | hurt_score (ΔEV loss) | ΔEV | ΔPF | ΔWR |",
        "|---|---:|---:|---:|---:|",
    ])
    for r in result.get("remove_one") or []:
        deg = r.get("degradation") or {}
        lines.append(
            f"| `{r.get('removed')}` | {r.get('hurt_score')} | "
            f"{deg.get('delta_ev')} | {deg.get('delta_pf')} | {deg.get('delta_wr')} |"
        )
    lines.extend([
        "",
        "## Brain without X",
        "",
        "| ablation | hurt_score | ΔEV |",
        "|---|---:|---:|",
    ])
    for r in result.get("brain_without") or []:
        deg = r.get("degradation") or {}
        lines.append(
            f"| {r.get('label') or r.get('removed')} | {r.get('hurt_score')} | {deg.get('delta_ev')} |"
        )
    worst = (result.get("brain_without") or [{}])[0] if result.get("brain_without") else {}
    lines.extend([
        "",
        f"**Hurts Brain most when removed:** `{worst.get('removed')}` "
        f"(hurt={worst.get('hurt_score')})",
        "",
        "## Grades",
        "",
        "| module | grade | score | keep |",
        "|---|---|---:|---|",
    ])
    for r in result.get("ranked") or []:
        lines.append(
            f"| `{r.get('module')}` | {r.get('grade')} | {r.get('score')} | "
            f"{'YES' if r.get('keep') else 'NO'} |"
        )
    lines.extend([
        "",
        "## Pareto",
        "",
        f"- modules capturing ~80% power: **{pareto.get('pareto_modules')}**",
        f"- fraction of modules: **{pareto.get('pareto_fraction')}** "
        f"({pareto.get('n_pareto')}/{pareto.get('n_all')})",
        f"- power captured: **{pareto.get('power_captured')}**",
        "",
        "## Simplification",
        "",
        f"- **keep:** {simp.get('modules_to_keep')}",
        f"- **remove:** {simp.get('modules_to_remove')}",
        f"- estimated EV gain after dropping REMOVE: "
        f"**{simp.get('estimated_ev_gain_after_simplification')}**",
        "",
        "## Complexity",
        "",
        "| module | runtime_ms | loc | edge/sec | edge/1k LOC |",
        "|---|---:|---:|---:|---:|",
    ])
    for r in result.get("complexity") or []:
        lines.append(
            f"| `{r.get('module')}` | {r.get('runtime_ms')} | {r.get('loc')} | "
            f"{r.get('edge_per_second')} | {r.get('edge_per_1000_loc')} |"
        )
    lines.extend([
        "",
        "## Integrity",
        "",
        f"- research_only: {result.get('research_only')}",
        f"- paper_unchanged: {result.get('paper_unchanged')}",
        f"- execution_unchanged: {result.get('execution_unchanged')}",
        f"- strategy_unchanged: {result.get('strategy_unchanged')}",
        f"- gate_unchanged: {result.get('gate_unchanged')}",
        f"- optimizer_unchanged: {result.get('optimizer_unchanged')}",
        f"- brain_unchanged: {result.get('brain_unchanged')}",
        "",
    ])
    return "\n".join(lines)


def format_attribution_md(result: dict[str, Any]) -> str:
    lines = [
        "# MODULE_ATTRIBUTION",
        "",
        "Per-module contribution on CLOSED S42 / research-lake trades vs production baseline.",
        "",
    ]
    lines.extend(_tbl_attribution(result.get("attribution") or []))
    lines.extend([
        "",
        "## Solo metrics",
        "",
        "```json",
        json.dumps(result.get("attribution") or [], indent=2, default=str)[:50_000],
        "```",
        "",
        "## Incremental chain",
        "",
        "```json",
        json.dumps(result.get("incremental") or [], indent=2, default=str)[:30_000],
        "```",
        "",
    ])
    return "\n".join(lines)


def format_redundancy_md(result: dict[str, Any]) -> str:
    red = result.get("redundancy") or {}
    lines = [
        "# MODULE_REDUNDANCY",
        "",
        "Correlation / MI / overlap / redundancy between module action streams.",
        "",
        "## Top redundant pairs",
        "",
        "| a | b | corr | MI | overlap | redundancy |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for p in red.get("top_redundant_pairs") or []:
        lines.append(
            f"| `{p.get('a')}` | `{p.get('b')}` | {p.get('correlation')} | "
            f"{p.get('mutual_information')} | {p.get('overlap')} | {p.get('redundancy')} |"
        )
    lines.extend([
        "",
        "## Correlation matrix",
        "",
        f"```json\n{json.dumps(red.get('correlation') or {}, indent=2)}\n```",
        "",
        "## Mutual information",
        "",
        f"```json\n{json.dumps(red.get('mutual_information') or {}, indent=2)}\n```",
        "",
        "## Overlap",
        "",
        f"```json\n{json.dumps(red.get('overlap') or {}, indent=2)}\n```",
        "",
        "## Redundancy",
        "",
        f"```json\n{json.dumps(red.get('redundancy') or {}, indent=2)}\n```",
        "",
    ])
    return "\n".join(lines)


def format_pareto_md(result: dict[str, Any]) -> str:
    pareto = result.get("pareto") or {}
    simp = result.get("simplification") or {}
    lines = [
        "# PARETO_REPORT",
        "",
        "Empirical 80% predictive-power prefix over ranked modules (by ΔEV / score).",
        "",
        f"- rule: {pareto.get('rule')}",
        f"- pareto modules: **{pareto.get('pareto_modules')}**",
        f"- n_pareto / n_all: **{pareto.get('n_pareto')} / {pareto.get('n_all')}** "
        f"(fraction={pareto.get('pareto_fraction')})",
        f"- power captured: **{pareto.get('power_captured')}**",
        "",
        "## Cumulative shares",
        "",
        "| module | contrib | cum_share |",
        "|---|---:|---:|",
    ]
    for s in pareto.get("shares") or []:
        lines.append(f"| `{s.get('module')}` | {s.get('contrib')} | {s.get('cum_share')} |")
    lines.extend([
        "",
        "## Keep / Remove",
        "",
        f"- keep: {simp.get('modules_to_keep')}",
        f"- remove: {simp.get('modules_to_remove')}",
        f"- estimated EV gain after simplification: "
        f"**{simp.get('estimated_ev_gain_after_simplification')}**",
        "",
        "## Ranked grades",
        "",
        "```json",
        json.dumps(result.get("ranked") or [], indent=2, default=str),
        "```",
        "",
    ])
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    edge = format_edge_reality_report(result)
    attr = format_attribution_md(result)
    red = format_redundancy_md(result)
    pareto = format_pareto_md(result)

    paths = {
        "EDGE_REALITY_REPORT.md": EDGE_REALITY_MD,
        "MODULE_ATTRIBUTION.md": ATTRIBUTION_MD,
        "MODULE_REDUNDANCY.md": REDUNDANCY_MD,
        "PARETO_REPORT.md": PARETO_MD,
        "out/EDGE_REALITY_REPORT.md": OUT_DIR / "EDGE_REALITY_REPORT.md",
        "out/MODULE_ATTRIBUTION.md": OUT_DIR / "MODULE_ATTRIBUTION.md",
        "out/MODULE_REDUNDANCY.md": OUT_DIR / "MODULE_REDUNDANCY.md",
        "out/PARETO_REPORT.md": OUT_DIR / "PARETO_REPORT.md",
    }
    EDGE_REALITY_MD.write_text(edge, encoding="utf-8")
    ATTRIBUTION_MD.write_text(attr, encoding="utf-8")
    REDUNDANCY_MD.write_text(red, encoding="utf-8")
    PARETO_MD.write_text(pareto, encoding="utf-8")
    (OUT_DIR / "EDGE_REALITY_REPORT.md").write_text(edge, encoding="utf-8")
    (OUT_DIR / "MODULE_ATTRIBUTION.md").write_text(attr, encoding="utf-8")
    (OUT_DIR / "MODULE_REDUNDANCY.md").write_text(red, encoding="utf-8")
    (OUT_DIR / "PARETO_REPORT.md").write_text(pareto, encoding="utf-8")
    (OUT_DIR / "edge_reality_result.json").write_text(
        json.dumps({
            k: result.get(k)
            for k in (
                "ok", "run_id", "n_trades", "elapsed_sec", "load_stats",
                "attribution", "incremental", "remove_one", "brain_without",
                "redundancy", "complexity", "ranked", "pareto", "simplification",
            )
        }, indent=2, default=str),
        encoding="utf-8",
    )
    return {k: str(v) for k, v in paths.items()}


__all__ = [
    "format_attribution_md",
    "format_edge_reality_report",
    "format_pareto_md",
    "format_redundancy_md",
    "write_artifacts",
]
