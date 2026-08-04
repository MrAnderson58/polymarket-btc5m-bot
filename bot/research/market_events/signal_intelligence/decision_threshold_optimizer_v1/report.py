"""Reports for Decision Threshold Optimizer V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.grid import (
    CONFIDENCE_GRID,
    FINGERPRINT_GRID,
    HIST_PF_GRID,
    HIST_WR_GRID,
    RULES_GRID,
    SUPPORTING_GRID,
    TIMELINE_GRID,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.pareto import (
    PROFILE_NAMES,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "decision_threshold_optimizer_v1"


def _pf(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def _profile_block(name: str, p: dict[str, Any]) -> list[str]:
    if not p:
        return [name, "  (none)", ""]
    return [
        name,
        f"  Trades     {p.get('trades')}",
        f"  WR         {p.get('wr')}",
        f"  PF         {_pf(p.get('pf'))}",
        f"  EV         {p.get('ev')}",
        f"  Sharpe     {p.get('sharpe')}",
        f"  MaxDD      {p.get('max_dd')}",
        f"  Rejected   {p.get('rejected')}",
        f"  Accepted   {p.get('accepted')}",
        f"  Top reject {p.get('top_rejection_reason')}",
        f"  Thresholds {p.get('label')}",
        "",
    ]


def format_terminal(result: dict[str, Any]) -> str:
    lines = [
        "DECISION THRESHOLD OPTIMIZER V1",
        "",
        f"grid={result.get('grid_size')} evaluated={result.get('n_evaluated')} "
        f"cache_n={result.get('cache_n')}",
        "",
        "BEFORE (baseline)",
    ]
    b = result.get("before") or {}
    lines.extend([
        f"  Trades {b.get('trades')} WR {b.get('wr')} PF {_pf(b.get('pf'))} "
        f"EV {b.get('ev')} Sharpe {b.get('sharpe')} MaxDD {b.get('max_dd')}",
        f"  FR {b.get('false_rejects')} FA {b.get('false_approvals')} "
        f"P {b.get('precision')} R {b.get('recall')} F1 {b.get('f1')}",
        "",
        "PROFILES",
        "",
    ])
    profiles = result.get("profiles") or {}
    for name in PROFILE_NAMES:
        lines.extend(_profile_block(name, profiles.get(name) or {}))
    best = result.get("best") or {}
    lines.extend([
        "BEST",
        f"  {best.get('label')}",
        f"  Trades {best.get('trades')} WR {best.get('wr')} PF {_pf(best.get('pf'))} "
        f"EV {best.get('ev')} F1 {best.get('f1')}",
        "",
        "AFTER vs BEFORE",
        f"  Δtrades={(best.get('trades') or 0) - (b.get('trades') or 0)} "
        f"ΔWR={_delta(best.get('wr'), b.get('wr'))} "
        f"ΔEV={_delta(best.get('ev'), b.get('ev'))} "
        f"ΔFR={_delta(best.get('false_rejects'), b.get('false_rejects'))}",
        "",
        f"elapsed={result.get('elapsed_sec')}s research_only=true",
    ])
    return "\n".join(lines)


def _delta(a: Any, b: Any) -> str:
    try:
        if a is None or b is None:
            return "—"
        return f"{float(a) - float(b):+.4f}"
    except Exception:
        return "—"


def format_threshold_report(result: dict[str, Any]) -> str:
    lines = [
        "# DECISION_THRESHOLD_REPORT",
        "",
        "_Decision Threshold Optimizer V1 — research only._",
        "",
        f"- grid size: {result.get('grid_size')}",
        f"- evaluated: {result.get('n_evaluated')}",
        f"- cache n: {result.get('cache_n')}",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        "",
        "## Before (baseline)",
        "",
        f"```\n{json.dumps(result.get('before') or {}, indent=2, default=str)}\n```",
        "",
        "## Best profile",
        "",
        f"```\n{json.dumps(result.get('best') or {}, indent=2, default=str)}\n```",
        "",
        "## Profiles",
        "",
    ]
    for name in PROFILE_NAMES:
        p = (result.get("profiles") or {}).get(name) or {}
        lines.append(f"### {name}")
        lines.append(f"- trades={p.get('trades')} WR={p.get('wr')} PF={_pf(p.get('pf'))} "
                     f"EV={p.get('ev')} Sharpe={p.get('sharpe')} MaxDD={p.get('max_dd')}")
        lines.append(f"- rejected={p.get('rejected')} accepted={p.get('accepted')}")
        lines.append(f"- top rejection={p.get('top_rejection_reason')}")
        lines.append(f"- `{p.get('label')}`")
        lines.append("")
    fr = result.get("false_reject_analysis") or {}
    lines.extend([
        "## Top 100 false rejects",
        "",
        f"- n false rejects: {fr.get('n')}",
        f"- top100 pnl left on table: {fr.get('top100_pnl')}",
        f"- binding counts: `{fr.get('binding_counts')}`",
        f"- EV recovered if relaxed: `{fr.get('ev_recovered_if_relaxed')}`",
        f"- mean pnl if relaxed: `{fr.get('mean_pnl_if_relaxed')}`",
        "",
    ])
    return "\n".join(lines)


def format_pareto_md(result: dict[str, Any]) -> str:
    lines = ["# PARETO_FRONTIER", "", f"- size: {len(result.get('pareto') or [])}", ""]
    for i, r in enumerate((result.get("pareto") or [])[:40], 1):
        lines.append(
            f"{i}. trades={r.get('trades')} WR={r.get('wr')} EV={r.get('ev')} "
            f"F1={r.get('f1')} FR={r.get('false_rejects')} — `{r.get('label')}`"
        )
    lines.append("")
    return "\n".join(lines)


def format_best_profile_md(result: dict[str, Any]) -> str:
    best = result.get("best") or {}
    before = result.get("before") or {}
    return "\n".join([
        "# BEST_PROFILE",
        "",
        f"**{best.get('label')}**",
        "",
        "## After",
        f"- Trades: {best.get('trades')}",
        f"- WR: {best.get('wr')}",
        f"- PF: {_pf(best.get('pf'))}",
        f"- EV: {best.get('ev')}",
        f"- Sharpe: {best.get('sharpe')}",
        f"- MaxDD: {best.get('max_dd')}",
        f"- F1: {best.get('f1')}",
        f"- False rejects: {best.get('false_rejects')}",
        f"- False approvals: {best.get('false_approvals')}",
        "",
        "## Before",
        f"- Trades: {before.get('trades')}",
        f"- WR: {before.get('wr')}",
        f"- PF: {_pf(before.get('pf'))}",
        f"- EV: {before.get('ev')}",
        f"- F1: {before.get('f1')}",
        f"- False rejects: {before.get('false_rejects')}",
        "",
        "## Thresholds JSON",
        "",
        f"```json\n{json.dumps(best.get('thresholds') or {}, indent=2)}\n```",
        "",
    ])


def format_heatmap_md(result: dict[str, Any]) -> str:
    """Slice heatmaps: mean EV by confidence × supporting (best other dims)."""
    results = result.get("all_results") or []
    lines = [
        "# THRESHOLD_HEATMAP",
        "",
        "Mean EV by confidence × supporting_modules (max over other dims).",
        "",
        "| conf \\ sup | " + " | ".join(str(s) for s in SUPPORTING_GRID) + " |",
        "|---|" + "|".join(["---"] * len(SUPPORTING_GRID)) + "|",
    ]
    # build max EV matrix
    best: dict[tuple[float, int], float] = {}
    for r in results:
        thr = r.get("thresholds") or {}
        key = (float(thr.get("min_confidence") or 0), int(thr.get("min_supporting") or 0))
        ev = r.get("ev")
        if ev is None:
            continue
        try:
            evf = float(ev)
        except Exception:
            continue
        if key not in best or evf > best[key]:
            best[key] = evf
    for conf in CONFIDENCE_GRID:
        row = [f"{int(conf * 100)}%"]
        for sup in SUPPORTING_GRID:
            v = best.get((float(conf), int(sup)))
            row.append(f"{v:.3f}" if v is not None else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.extend([
        "",
        "Grids:",
        f"- supporting: {SUPPORTING_GRID}",
        f"- confidence: {CONFIDENCE_GRID}",
        f"- fingerprint: {FINGERPRINT_GRID}",
        f"- timeline: {TIMELINE_GRID}",
        f"- hist_wr: {HIST_WR_GRID}",
        f"- hist_pf: {HIST_PF_GRID}",
        f"- rules: {RULES_GRID}",
        "",
    ])
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = {
        "decision_threshold_report": str(BASE_DIR / "DECISION_THRESHOLD_REPORT.md"),
        "pareto_frontier": str(BASE_DIR / "PARETO_FRONTIER.md"),
        "threshold_heatmap": str(BASE_DIR / "THRESHOLD_HEATMAP.md"),
        "best_profile": str(BASE_DIR / "BEST_PROFILE.md"),
        "out_report": str(OUT_DIR / "DECISION_THRESHOLD_REPORT.md"),
        "out_pareto": str(OUT_DIR / "PARETO_FRONTIER.md"),
        "out_heatmap": str(OUT_DIR / "THRESHOLD_HEATMAP.md"),
        "out_best": str(OUT_DIR / "BEST_PROFILE.md"),
        "out_json": str(OUT_DIR / "threshold_optimize.json"),
    }
    report = format_threshold_report(result)
    pareto = format_pareto_md(result)
    heatmap = format_heatmap_md(result)
    best = format_best_profile_md(result)
    Path(paths["decision_threshold_report"]).write_text(report, encoding="utf-8")
    Path(paths["pareto_frontier"]).write_text(pareto, encoding="utf-8")
    Path(paths["threshold_heatmap"]).write_text(heatmap, encoding="utf-8")
    Path(paths["best_profile"]).write_text(best, encoding="utf-8")
    Path(paths["out_report"]).write_text(report, encoding="utf-8")
    Path(paths["out_pareto"]).write_text(pareto, encoding="utf-8")
    Path(paths["out_heatmap"]).write_text(heatmap, encoding="utf-8")
    Path(paths["out_best"]).write_text(best, encoding="utf-8")
    slim = {k: v for k, v in result.items() if k not in ("all_results", "terminal")}
    # keep pareto + profiles + before/best + false reject summary
    Path(paths["out_json"]).write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    return paths


__all__ = [
    "format_best_profile_md",
    "format_heatmap_md",
    "format_pareto_md",
    "format_terminal",
    "format_threshold_report",
    "write_artifacts",
]
