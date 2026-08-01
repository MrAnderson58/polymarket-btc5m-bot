"""Reports for Market Causality Engine V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.market_causality_v1.graph import (
    graph_markdown,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "market_causality_v1"
REPORT_MD = BASE_DIR / "CAUSALITY_REPORT.md"
CLUSTERS_MD = BASE_DIR / "CAUSE_CLUSTERS.md"
COUNTERFACTUAL_MD = BASE_DIR / "COUNTERFACTUAL_REPORT.md"


def format_causality_report(result: dict[str, Any]) -> str:
    graph = result.get("graph") or {}
    stab = result.get("stability") or {}
    lines = [
        "# CAUSALITY_REPORT",
        "",
        "_Market Causality Engine V1 — from correlation to causation. "
        "Research only. Gate / Strategy / Paper / Optimizer / Execution unchanged._",
        "",
        "## Run",
        "",
        f"- trades analyzed: **{result.get('n_rows')}**",
        f"- causal rows: **{result.get('n_causal')}**",
        f"- causal relations (edges): **{graph.get('n_edges')}**",
        f"- coverage: **{result.get('coverage_pct')}%**",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- lake source: `{(result.get('load_stats') or {}).get('source')}`",
        f"- temporal_leakage_rejected: {result.get('temporal_leakage_rejected')}",
        "",
        "## Dominant causes",
        "",
    ]
    for k, v in (result.get("dominant_causes") or {}).items():
        lines.append(f"- `{k}`: {v}%")
    lines.extend(["", "## Causal graph (top edges)", ""])
    lines.extend(graph_markdown(graph))
    lines.extend([
        "",
        "## Stability statistics",
        "",
        f"- overall stable: **{stab.get('stable')}**",
        f"- checks passed: {stab.get('n_checks_passed')}/4",
        f"- walk-forward: {json.dumps(stab.get('walk_forward') or {}, default=str)}",
        f"- bootstrap: {json.dumps(stab.get('bootstrap') or {}, default=str)}",
        f"- permutation: {json.dumps(stab.get('permutation') or {}, default=str)}",
        f"- regimes: {json.dumps((stab.get('regimes') or {}).get('stability'), default=str)}",
        "",
        "## Integrity",
        "",
        f"- gate_unchanged: {result.get('gate_unchanged')}",
        f"- paper_unchanged: {result.get('paper_unchanged')}",
        f"- optimizer_unchanged: {result.get('optimizer_unchanged')}",
        f"- execution_unchanged: {result.get('execution_unchanged')}",
        f"- no_n_plus_1_sql: {result.get('no_n_plus_1_sql')}",
        "",
    ])
    return "\n".join(lines)


def format_clusters_md(result: dict[str, Any]) -> str:
    lines = [
        "# CAUSE_CLUSTERS",
        "",
        "Trades clustered by causal explanation.",
        "",
        "| cluster | n | share | mean_pnl | primary_mode |",
        "|---|---|---|---|---|",
    ]
    for c in (result.get("top_clusters") or []):
        lines.append(
            f"| `{c.get('cluster')}` | {c.get('n')} | {c.get('share')} | "
            f"{c.get('mean_pnl')} | {c.get('primary_mode')} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_counterfactual_md(result: dict[str, Any]) -> str:
    lines = [
        "# COUNTERFACTUAL_REPORT",
        "",
        "Portfolio ablation: metrics if high-contribution trades for a feature are removed.",
        "",
        "| feature | ΔEV | ΔWR | ΔPF | n_ablated |",
        "|---|---|---|---|---|",
    ]
    for c in (result.get("portfolio_counterfactuals") or []):
        lines.append(
            f"| `{c.get('feature')}` | {c.get('delta_ev')} | {c.get('delta_wr')} | "
            f"{c.get('delta_pf')} | {c.get('n_ablated')} |"
        )
    lines.extend(["", "## Per-trade examples", ""])
    for ex in (result.get("counterfactual_examples") or [])[:5]:
        lines.append(f"### trade_id={ex.get('trade_id')} primary={ex.get('primary_cause')}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(ex.get("counterfactual") or {}, indent=2, default=str))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    report = format_causality_report(result)
    REPORT_MD.write_text(report, encoding="utf-8")
    (OUT_DIR / "CAUSALITY_REPORT.md").write_text(report, encoding="utf-8")
    paths["report_md"] = str(REPORT_MD)

    clusters = format_clusters_md(result)
    CLUSTERS_MD.write_text(clusters, encoding="utf-8")
    (OUT_DIR / "CAUSE_CLUSTERS.md").write_text(clusters, encoding="utf-8")
    paths["clusters_md"] = str(CLUSTERS_MD)

    cf = format_counterfactual_md(result)
    COUNTERFACTUAL_MD.write_text(cf, encoding="utf-8")
    (OUT_DIR / "COUNTERFACTUAL_REPORT.md").write_text(cf, encoding="utf-8")
    paths["counterfactual_md"] = str(COUNTERFACTUAL_MD)

    payloads = {
        "graph.json": result.get("graph") or {},
        "stability.json": result.get("stability") or {},
        "clusters.json": result.get("clusters") or {},
        "dominant_causes.json": result.get("dominant_causes") or {},
        "portfolio_counterfactuals.json": result.get("portfolio_counterfactuals") or [],
        "summary.json": {
            "n_rows": result.get("n_rows"),
            "n_causal": result.get("n_causal"),
            "n_edges": (result.get("graph") or {}).get("n_edges"),
            "coverage_pct": result.get("coverage_pct"),
            "elapsed_sec": result.get("elapsed_sec"),
            "stable": (result.get("stability") or {}).get("stable"),
        },
    }
    for name, payload in payloads.items():
        p = OUT_DIR / name
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[name] = str(p)
    return paths


__all__ = [
    "OUT_DIR",
    "format_causality_report",
    "format_clusters_md",
    "format_counterfactual_md",
    "write_artifacts",
]
