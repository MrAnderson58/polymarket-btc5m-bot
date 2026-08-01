"""Write Edge Discovery artifacts (research-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPORT_MD = BASE_DIR / "EDGE_DISCOVERY_REPORT.md"
OUT_DIR = BASE_DIR / "reports" / "research" / "edge_discovery"


def _status_emoji(status: str) -> str:
    return {"READY": "READY", "TEST": "TEST", "REJECT": "REJECT"}.get(status, status)


def format_report(result: dict[str, Any]) -> str:
    base = result.get("baseline") or {}
    top = result.get("candidates") or []
    lines = [
        "# EDGE_DISCOVERY_REPORT",
        "",
        "_Edge Discovery Engine V1 — combinatorial mathematics only. "
        "No Gate / Optimizer / Strategy / Paper / Execution changes._",
        "",
        f"- Trades analyzed: **{result.get('n_rows')}** (S42 INNER JOIN S55)",
        f"- Combos tested: **{result.get('n_combos_tested')}** "
        f"(atoms={result.get('n_atoms')})",
        f"- Min n applied: **{result.get('min_n_applied')}** "
        f"(research target={result.get('research_min_n')})",
        f"- READY={result.get('n_ready')} TEST={result.get('n_test')}",
        f"- Baseline PF={base.get('pf')} EV={base.get('expectancy')} "
        f"WR={base.get('winrate')} Sharpe={base.get('sharpe')}",
        f"- Elapsed: {result.get('elapsed_sec')}s",
        "",
        "## TOP-100 Edge Scoreboard",
        "",
        "| Rank | Edge score | Rule | n | PF | EV | WR | p | CI | Status |",
        "|---:|---:|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for i, c in enumerate(top[:100], 1):
        ci = c.get("ci95") or (None, None)
        ci_s = f"[{ci[0]}, {ci[1]}]" if ci[0] is not None else "—"
        rule = str(c.get("rule") or "")
        if len(rule) > 80:
            rule = rule[:77] + "..."
        lines.append(
            f"| {i} | {c.get('edge_score')} | `{rule}` | {c.get('n')} | "
            f"{c.get('pf')} | {c.get('expectancy')} | {c.get('winrate')} | "
            f"{c.get('p_value')} | {ci_s} | {_status_emoji(str(c.get('status')))} |"
        )
    if not top:
        lines.append("| — | — | _(no edges passed filters on this corpus)_ | — | — | — | — | — | — | — |")

    clusters = (result.get("clusters") or {}).get("clusters") or {}
    lines.extend(["", "## Clusters", ""])
    for name, info in clusters.items():
        lines.append(
            f"- **{name}**: n_rules={info.get('n_rules')} best_score={info.get('best_score')}"
        )
    lines.extend([
        "",
        "## Notes",
        "",
        "- Filters: n floor, PF/EV vs baseline, p≤0.05, FDR, instability, overlap>80%.",
        "- Status READY requires research n≥100 + FDR + WF + OOS + stability.",
        "- Status TEST is exploratory on smaller local books.",
        "",
        "Artifacts under `reports/research/edge_discovery/`.",
        "",
    ])
    return "\n".join(lines)


def format_heatmaps(result: dict[str, Any]) -> str:
    lines = [
        "# Edge Discovery Heatmaps",
        "",
        "## Feature frequency in TOP edges",
        "",
    ]
    freq: dict[str, int] = {}
    for c in result.get("candidates") or []:
        for f in c.get("features") or []:
            freq[str(f)] = freq.get(str(f), 0) + 1
    for f, n in sorted(freq.items(), key=lambda x: -x[1]):
        bar = "#" * min(40, n)
        lines.append(f"- `{f}`: {n} {bar}")
    lines.extend(["", "## Cluster sizes", ""])
    clusters = (result.get("clusters") or {}).get("clusters") or {}
    for name, info in clusters.items():
        n = int(info.get("n_rules") or 0)
        lines.append(f"- {name}: {n} {'#' * min(40, n)}")
    lines.append("")
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = result.get("candidates") or []
    scoreboard = []
    for i, c in enumerate(candidates[:100], 1):
        scoreboard.append({
            "rank": i,
            "edge_score": c.get("edge_score"),
            "rule": c.get("rule"),
            "n": c.get("n"),
            "pf": c.get("pf"),
            "ev": c.get("expectancy"),
            "wr": c.get("winrate"),
            "p": c.get("p_value"),
            "ci": c.get("ci95"),
            "status": c.get("status"),
            "cluster": c.get("cluster"),
            "sharpe": c.get("sharpe"),
            "max_dd": c.get("max_dd"),
            "q_value": c.get("q_value"),
            "wf_ok": c.get("wf_ok"),
            "oos_ok": c.get("oos_ok"),
            "stability": c.get("stability"),
        })

    paths: dict[str, str] = {}
    mapping = {
        "edge_rules.json": candidates,
        "edge_clusters.json": result.get("clusters"),
        "edge_scoreboard.json": scoreboard,
    }
    for name, payload in mapping.items():
        p = OUT_DIR / name
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[name] = str(p)

    heat = format_heatmaps(result)
    heat_path = OUT_DIR / "edge_heatmaps.md"
    heat_path.write_text(heat, encoding="utf-8")
    paths["edge_heatmaps.md"] = str(heat_path)

    md = format_report(result)
    REPORT_MD.write_text(md, encoding="utf-8")
    copy = OUT_DIR / "EDGE_DISCOVERY_REPORT.md"
    copy.write_text(md, encoding="utf-8")
    paths["report_md"] = str(REPORT_MD)
    paths["report_copy"] = str(copy)
    return paths


__all__ = ["format_report", "write_artifacts"]
