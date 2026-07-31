"""Reports, heatmaps, clusters for Alpha Discovery Engine V1."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.stats import (
    benjamini_hochberg,
    bonferroni,
    effective_pf,
)

REPORT_MD = BASE_DIR / "ALPHA_DISCOVERY_REPORT.md"
OUT_DIR = BASE_DIR / "reports" / "research" / "alpha_discovery_v1"


def rank_candidates(candidates: list[dict[str, Any]], *, fdr_alpha: float = 0.10) -> list[dict[str, Any]]:
    pvals = [c.get("p_value") for c in candidates]
    qvals = benjamini_hochberg(pvals, alpha=fdr_alpha)
    bvals = bonferroni(pvals)
    ranked = []
    for c, q, b in zip(candidates, qvals, bvals):
        row = dict(c)
        row["q_value_fdr"] = q
        row["p_value_bonferroni"] = b
        # score: expectancy lift + PF lift + significance
        ev = float(c.get("expectancy_delta") or c.get("expectancy") or 0.0)
        pf_d = float(c.get("pf_delta") or 0.0)
        sig = 0.0
        if q is not None:
            sig = max(0.0, 1.0 - float(q))
        elif c.get("p_value") is not None:
            sig = max(0.0, 1.0 - float(c["p_value"]))
        n_term = min(1.0, math.log1p(float(c.get("n") or 0)) / math.log1p(200))
        row["alpha_score"] = round(0.45 * ev + 0.25 * pf_d + 0.20 * sig + 0.10 * n_term, 4)
        row["significant_fdr"] = bool(q is not None and q <= fdr_alpha)
        ranked.append(row)
    ranked.sort(
        key=lambda r: (
            not r.get("significant_fdr"),
            -(r.get("alpha_score") or -1e9),
            -(r.get("expectancy") or -1e9),
        ),
    )
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked


def build_heatmaps(ranked: list[dict[str, Any]]) -> dict[str, Any]:
    """Pairwise feature co-occurrence heat among top alphas."""
    top = ranked[:100]
    pair_scores: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    pair_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in top:
        feats = list(c.get("features") or [])
        if len(feats) < 2:
            # self
            f = feats[0] if feats else "unknown"
            pair_scores[f][f] += float(c.get("alpha_score") or 0)
            pair_counts[f][f] += 1
            continue
        for i, a in enumerate(feats):
            for b in feats[i:]:
                pair_scores[a][b] += float(c.get("alpha_score") or 0)
                pair_counts[a][b] += 1
                if a != b:
                    pair_scores[b][a] += float(c.get("alpha_score") or 0)
                    pair_counts[b][a] += 1
    labels = sorted({f for c in top for f in (c.get("features") or [])})
    matrix = []
    for a in labels:
        row = []
        for b in labels:
            n = pair_counts[a][b]
            row.append(None if n == 0 else round(pair_scores[a][b] / n, 4))
        matrix.append(row)
    return {"labels": labels, "matrix": matrix, "n_top": len(top)}


def build_clusters(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster alphas by shared primary feature set."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in ranked[:200]:
        key = "+".join(sorted(c.get("features") or ["unknown"]))
        groups[key].append(c)
    clusters = []
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        best = items[0]
        clusters.append({
            "cluster_id": key,
            "size": len(items),
            "best_label": best.get("label"),
            "best_score": best.get("alpha_score"),
            "best_expectancy": best.get("expectancy"),
            "best_pf": best.get("pf"),
            "best_n": best.get("n"),
            "n_significant_fdr": sum(1 for x in items if x.get("significant_fdr")),
            "members": [x.get("id") for x in items[:20]],
        })
    clusters.sort(key=lambda c: (-(c.get("best_score") or -1e9), -c["size"]))
    return clusters


def format_report(
    *,
    result: dict[str, Any],
    ranked: list[dict[str, Any]],
    heatmaps: dict[str, Any],
    clusters: list[dict[str, Any]],
) -> str:
    base = result.get("baseline") or {}
    lines = [
        "# ALPHA_DISCOVERY_REPORT",
        "",
        "_Alpha Discovery Engine V1 — research only. No Gate / Trading / Paper / Optimizer / Execution changes._",
        "",
        f"- Corpus trades: **{result.get('n_rows')}**",
        f"- Atomic rules: **{result.get('n_atoms')}**",
        f"- Rules tested: **{result.get('n_rules_tested')}**",
        f"- Candidates after filters: **{len(ranked)}**",
        f"- Significant (FDR≤0.10): **{sum(1 for r in ranked if r.get('significant_fdr'))}**",
        f"- min_n: **{result.get('min_n')}**",
        "",
        "> **Note:** With a small local book (n≪1000), many FDR hits are exploratory. "
        "Re-run on the full S42 corpus (`ALPHA_ENGINE_LIMIT=100000`) before promoting any alpha.",
        "",
        "## Baseline (all trades)",
        "",
        f"- n={base.get('n')} WR={base.get('winrate')} EV={base.get('expectancy')} "
        f"PF={base.get('pf')} Sharpe={base.get('sharpe')}",
        "",
        "## Top alpha candidates",
        "",
        "| Rank | Score | Label | n | WR | EV | PF | Sharpe | CI | p | q(FDR) |",
        "|---:|---:|---|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for r in ranked[:30]:
        lines.append(
            "| {rank} | {score} | `{label}` | {n} | {wr} | {ev} | {pf} | {sh} | {ci} | {p} | {q} |".format(
                rank=r.get("rank"),
                score=r.get("alpha_score"),
                label=(r.get("label") or "")[:80],
                n=r.get("n"),
                wr=r.get("winrate"),
                ev=r.get("expectancy"),
                pf=r.get("pf"),
                sh=r.get("sharpe"),
                ci=r.get("ci_ev"),
                p=r.get("p_value"),
                q=r.get("q_value_fdr"),
            )
        )
    lines.extend(["", "## Alpha clusters", ""])
    for c in clusters[:15]:
        lines.append(
            f"- **`{c['cluster_id']}`** size={c['size']} best_EV={c.get('best_expectancy')} "
            f"best_PF={c.get('best_pf')} score={c.get('best_score')} "
            f"sig_fdr={c.get('n_significant_fdr')}"
        )
        lines.append(f"  - {c.get('best_label')}")
    lines.extend([
        "",
        "## Heatmap summary",
        "",
        f"- Features in top-100 co-occurrence: {', '.join((heatmaps.get('labels') or [])[:20])}",
        "",
        "## Safety",
        "",
        "- Observe-only research module",
        "- No auto-apply to Gate / Trading / Paper / Optimizer / Execution",
        "",
    ])
    return "\n".join(lines)


def write_artifacts(
    *,
    result: dict[str, Any],
    ranked: list[dict[str, Any]],
) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    heatmaps = build_heatmaps(ranked)
    clusters = build_clusters(ranked)
    md = format_report(result=result, ranked=ranked, heatmaps=heatmaps, clusters=clusters)

    REPORT_MD.write_text(md, encoding="utf-8")
    candidates_path = OUT_DIR / "alpha_candidates.json"
    heat_path = OUT_DIR / "alpha_heatmaps.json"
    heat_md = OUT_DIR / "alpha_heatmaps.md"
    cluster_path = OUT_DIR / "alpha_clusters.json"
    report_copy = OUT_DIR / "ALPHA_DISCOVERY_REPORT.md"

    candidates_path.write_text(
        json.dumps({
            "n_rows": result.get("n_rows"),
            "baseline": result.get("baseline"),
            "min_n": result.get("min_n"),
            "n_rules_tested": result.get("n_rules_tested"),
            "candidates": ranked[:500],
        }, indent=2, default=str),
        encoding="utf-8",
    )
    heat_path.write_text(json.dumps(heatmaps, indent=2, default=str), encoding="utf-8")
    cluster_path.write_text(json.dumps(clusters, indent=2, default=str), encoding="utf-8")
    report_copy.write_text(md, encoding="utf-8")

    # Markdown heatmap table
    labels = heatmaps.get("labels") or []
    matrix = heatmaps.get("matrix") or []
    hlines = ["# Alpha Heatmaps V1", "", "Mean alpha_score co-occurrence (top candidates)", "", "| |" + "|".join(f" {l} " for l in labels) + "|", "|" + "|".join(["---"] * (len(labels) + 1)) + "|"]
    for lab, row in zip(labels, matrix):
        cells = " | ".join("—" if v is None else str(v) for v in row)
        hlines.append(f"| {lab} | {cells} |")
    heat_md.write_text("\n".join(hlines) + "\n", encoding="utf-8")

    return {
        "report_md": str(REPORT_MD),
        "candidates_json": str(candidates_path),
        "heatmaps_json": str(heat_path),
        "heatmaps_md": str(heat_md),
        "clusters_json": str(cluster_path),
        "report_copy": str(report_copy),
    }


__all__ = ["rank_candidates", "write_artifacts", "build_heatmaps", "build_clusters"]
