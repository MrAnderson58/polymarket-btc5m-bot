"""Reports and JSON artifacts for Market Edge Discovery V3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "edge_discovery_v3"

REPORTS = {
    "discovery": BASE_DIR / "EDGE_DISCOVERY_V3.md",
    "library": BASE_DIR / "EDGE_LIBRARY.md",
    "heatmap": BASE_DIR / "EDGE_HEATMAP.md",
    "stability": BASE_DIR / "EDGE_STABILITY.md",
    "importance": BASE_DIR / "FEATURE_IMPORTANCE.md",
    "regime": BASE_DIR / "REGIME_CLUSTER_REPORT.md",
}


def _top_table(edges: list[dict[str, Any]], n: int = 20) -> list[str]:
    lines = [
        "| # | score | n | EV | PF | WR | p | P(edge>0) | status | rule |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, e in enumerate(edges[:n], 1):
        rule = str(e.get("rule") or "")[:80]
        lines.append(
            f"| {i} | {e.get('quality_score')} | {e.get('n')} | {e.get('expectancy')} | "
            f"{e.get('pf')} | {e.get('winrate')} | {e.get('p_value')} | "
            f"{e.get('prob_edge_gt_0')} | {e.get('status')} | `{rule}` |"
        )
    if len(edges) == 0:
        lines.append("| — | — | — | — | — | — | — | — | — | (none) |")
    return lines


def format_discovery_md(result: dict[str, Any]) -> str:
    lines = [
        "# EDGE_DISCOVERY_V3",
        "",
        "_Market Edge Discovery V3 — combinatorial mining + Bayesian validation. "
        "Research only. Gate / Strategy / Optimizer / Paper / Execution unchanged._",
        "",
        "## Run",
        "",
        f"- trades analyzed: **{result.get('n_rows')}**",
        f"- edges tested: **{result.get('n_edges_tested')}**",
        f"- combinatorial search space (est.): **{result.get('n_search_space_estimate')}**",
        f"- screened / full-validated: {result.get('n_screened')} / {result.get('n_full_validated')}",
        f"- surviving: **{result.get('n_surviving')}** "
        f"(READY={result.get('n_ready')} TEST={result.get('n_test')})",
        f"- atoms / features: {result.get('n_atoms')} / {result.get('n_features')}",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- lake source: `{(result.get('load_stats') or {}).get('source')}`",
        "",
        "## Baseline",
        "",
        f"```json\n{json.dumps(result.get('baseline') or {}, indent=2)}\n```",
        "",
        "## TOP-20 statistically strongest edges",
        "",
    ]
    lines.extend(_top_table(result.get("top20") or result.get("candidates") or [], 20))
    lines.extend([
        "",
        "## Interaction highlights",
        "",
    ])
    ints = result.get("interactions") or []
    if not ints:
        lines.append("- (none above lift threshold)")
    else:
        for x in ints[:15]:
            lines.append(
                f"- lift={x.get('lift_vs_best_alone')} joint_EV={x.get('joint_ev')} "
                f"alone={x.get('alone_evs')} n={x.get('n')} — `{x.get('rule')}`"
            )
    lines.extend(["", "## Integrity", ""])
    for k in (
        "gate_unchanged", "optimizer_unchanged", "strategy_unchanged",
        "paper_unchanged", "execution_unchanged", "no_n_plus_1_sql",
    ):
        lines.append(f"- {k}: {result.get(k)}")
    lines.append("")
    return "\n".join(lines)


def format_library_md(result: dict[str, Any]) -> str:
    lib = result.get("library_rows") or result.get("candidates") or []
    lines = [
        "# EDGE_LIBRARY",
        "",
        f"SQLite table `market_edge_library_v1` — upserted **{result.get('library_upserted', 0)}** rows.",
        "",
    ]
    lines.extend(_top_table(lib, 50))
    lines.append("")
    return "\n".join(lines)


def format_heatmap_md(result: dict[str, Any]) -> str:
    lines = [
        "# EDGE_HEATMAP",
        "",
        "Feature co-occurrence among surviving edges (count).",
        "",
    ]
    feats: dict[str, int] = {}
    pairs: dict[tuple[str, str], int] = {}
    for e in result.get("candidates") or []:
        fs = [str(x) for x in (e.get("features") or [])]
        for f in fs:
            feats[f] = feats.get(f, 0) + 1
        for a, b in ((x, y) for i, x in enumerate(fs) for y in fs[i + 1 :]):
            key = tuple(sorted((a, b)))
            pairs[key] = pairs.get(key, 0) + 1
    lines.append("## Feature frequency")
    lines.append("")
    for f, n in sorted(feats.items(), key=lambda t: -t[1])[:30]:
        lines.append(f"- `{f}`: {n}")
    lines.append("")
    lines.append("## Top feature pairs")
    lines.append("")
    for (a, b), n in sorted(pairs.items(), key=lambda t: -t[1])[:30]:
        lines.append(f"- `{a}` × `{b}`: {n}")
    lines.append("")
    return "\n".join(lines)


def format_stability_md(result: dict[str, Any]) -> str:
    lines = [
        "# EDGE_STABILITY",
        "",
        "Walk-forward / rolling / expanding / OOS / regime-slice survival.",
        "",
        "| rule | WF | rolling | expanding | OOS | regime | stability | CV |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in (result.get("candidates") or [])[:40]:
        rule = str(e.get("rule") or "")[:60]
        lines.append(
            f"| `{rule}` | {e.get('wf_ok')} | {e.get('rolling_ok')} | "
            f"{e.get('expanding_ok')} | {e.get('oos_ok')} | {e.get('regime_ok')} | "
            f"{e.get('stability')} | {e.get('cv')} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_importance_md(result: dict[str, Any]) -> str:
    imp = result.get("importance") or {}
    lines = [
        "# FEATURE_IMPORTANCE",
        "",
        "Consensus ranking: Mutual Information + Information Gain + "
        "Permutation Importance + SHAP (if installed).",
        "",
        f"- shap_available: {imp.get('shap_available')}",
        "",
        "| rank | feature | consensus | MI | IG | perm | SHAP |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, row in enumerate(imp.get("ranking") or [], 1):
        lines.append(
            f"| {i} | `{row.get('feature')}` | {row.get('consensus')} | "
            f"{row.get('mutual_information')} | {row.get('information_gain')} | "
            f"{row.get('permutation_importance')} | {row.get('shap')} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_regime_md(result: dict[str, Any]) -> str:
    reg = result.get("regimes") or {}
    lines = [
        "# REGIME_CLUSTER_REPORT",
        "",
        "Automatic market-state clustering (KMeans + optional HDBSCAN; PCA/UMAP embed).",
        "",
        f"- ok: {reg.get('ok')}",
        f"- method: `{reg.get('method')}`",
        f"- hdbscan: {reg.get('hdbscan')}",
        f"- embedding: {reg.get('embedding_method')}",
        f"- n_regimes: {reg.get('n_regimes')}",
        f"- features_used: {', '.join(reg.get('features_used') or [])}",
        "",
        "## Regimes",
        "",
    ]
    for r in reg.get("regimes") or []:
        lines.append(
            f"### {r.get('label')} (n={r.get('n')}, share={r.get('share')})"
        )
        lines.append("")
        lines.append(
            f"- mean_pnl={r.get('mean_pnl')} winrate={r.get('winrate')}%"
        )
        lines.append("- top features:")
        for t in r.get("top_features") or []:
            lines.append(f"  - `{t.get('feature')}` z={t.get('z_mean')}")
        lines.append("")
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    md_map = {
        "discovery": format_discovery_md(result),
        "library": format_library_md(result),
        "heatmap": format_heatmap_md(result),
        "stability": format_stability_md(result),
        "importance": format_importance_md(result),
        "regime": format_regime_md(result),
    }
    for key, md in md_map.items():
        root = REPORTS[key]
        root.write_text(md, encoding="utf-8")
        copy = OUT_DIR / root.name
        copy.write_text(md, encoding="utf-8")
        paths[key] = str(root)
        paths[f"{key}_copy"] = str(copy)

    json_files = {
        "edges.json": result.get("candidates") or [],
        "top20.json": result.get("top20") or [],
        "interactions.json": result.get("interactions") or [],
        "importance.json": result.get("importance") or {},
        "regimes.json": result.get("regimes") or {},
        "summary.json": {
            "n_rows": result.get("n_rows"),
            "n_edges_tested": result.get("n_edges_tested"),
            "n_surviving": result.get("n_surviving"),
            "n_ready": result.get("n_ready"),
            "n_test": result.get("n_test"),
            "elapsed_sec": result.get("elapsed_sec"),
            "baseline": result.get("baseline"),
        },
    }
    for name, payload in json_files.items():
        p = OUT_DIR / name
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[name] = str(p)

    paths["report_md"] = paths["discovery"]
    return paths


__all__ = ["OUT_DIR", "REPORTS", "format_discovery_md", "write_artifacts"]
