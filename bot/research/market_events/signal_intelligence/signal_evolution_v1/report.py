"""Reports for Signal Evolution Engine V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "signal_evolution_v1"
REPORT_MD = BASE_DIR / "SIGNAL_EVOLUTION_REPORT.md"
DECAY_MD = BASE_DIR / "SIGNAL_DECAY.md"
DRIFT_MD = BASE_DIR / "FEATURE_DRIFT.md"


def format_evolution_report(result: dict[str, Any]) -> str:
    lines = [
        "# SIGNAL_EVOLUTION_REPORT",
        "",
        "_Signal Evolution Engine V1 — research-only. "
        "Does not modify Paper / Strategy / Execution / Gate / Optimizer._",
        "",
        "## Run",
        "",
        f"- tracked signals: **{result.get('n_signals')}**",
        f"- occurrences: **{result.get('n_occurrences')}**",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- mean update latency: **{result.get('mean_update_ms')} ms**",
        f"- source: `{(result.get('load_stats') or {}).get('source')}`",
        "",
        "## Strongest signals",
        "",
        "| signal | score | status | half_life | drift | confidence |",
        "|---|---|---|---|---|---|",
    ]
    for r in (result.get("strongest") or [])[:15]:
        lines.append(
            f"| `{r.get('signal')}` | {r.get('score')} | {r.get('status')} | "
            f"{r.get('half_life')} | {r.get('drift')} | {r.get('confidence')} |"
        )
    lines.extend([
        "",
        "## Weakest signals",
        "",
        "| signal | score | status | half_life | drift | confidence |",
        "|---|---|---|---|---|---|",
    ])
    for r in (result.get("weakest") or [])[:15]:
        lines.append(
            f"| `{r.get('signal')}` | {r.get('score')} | {r.get('status')} | "
            f"{r.get('half_life')} | {r.get('drift')} | {r.get('confidence')} |"
        )
    lines.extend([
        "",
        "## Lifecycle counts",
        "",
        f"```json\n{json.dumps(result.get('lifecycle_counts') or {}, indent=2)}\n```",
        "",
        "## Drift statistics",
        "",
        f"```json\n{json.dumps(result.get('drift_stats') or {}, indent=2)}\n```",
        "",
        "## Promotion",
        "",
        f"```json\n{json.dumps(result.get('promotion') or {}, indent=2)}\n```",
        "",
        "## Integrity",
        "",
        f"- research_only: {result.get('research_only')}",
        f"- gate_unchanged: {result.get('gate_unchanged')}",
        f"- strategy_unchanged: {result.get('strategy_unchanged')}",
        f"- paper_unchanged: {result.get('paper_unchanged')}",
        f"- execution_unchanged: {result.get('execution_unchanged')}",
        f"- optimizer_unchanged: {result.get('optimizer_unchanged')}",
        "",
    ])
    return "\n".join(lines)


def format_decay_md(result: dict[str, Any]) -> str:
    lines = [
        "# SIGNAL_DECAY",
        "",
        "Edge today vs 30d/90d ago, slope, half-life.",
        "",
        "| signal | status | edge_today | edge_30d | edge_90d | slope | half_life_days | survival |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in (result.get("ranked") or [])[:40]:
        d = r.get("decay") or {}
        s = r.get("survival") or {}
        lines.append(
            f"| `{r.get('signal')}` | {r.get('status')} | {d.get('edge_today')} | "
            f"{d.get('edge_30d_ago')} | {d.get('edge_90d_ago')} | {d.get('slope')} | "
            f"{d.get('half_life_days')} | {s.get('survival_prob')} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_drift_md(result: dict[str, Any]) -> str:
    lines = [
        "# FEATURE_DRIFT",
        "",
        "Distribution / concept / target / feature drift by signal.",
        "",
        "| signal | drift_score | level | distribution | concept | target | top feature drifts |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in (result.get("ranked") or [])[:40]:
        d = r.get("drift_detail") or {}
        feats = d.get("feature_drift") or {}
        top = ", ".join(
            f"{k}={v}"
            for k, v in sorted(feats.items(), key=lambda kv: -kv[1])[:3]
        ) or "-"
        lines.append(
            f"| `{r.get('signal')}` | {d.get('drift_score')} | {d.get('drift_level')} | "
            f"{d.get('distribution_drift')} | {d.get('concept_drift')} | "
            f"{d.get('target_drift')} | {top} |"
        )
    lines.extend([
        "",
        "## Aggregate",
        "",
        f"```json\n{json.dumps(result.get('drift_stats') or {}, indent=2)}\n```",
        "",
    ])
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    report = format_evolution_report(result)
    REPORT_MD.write_text(report, encoding="utf-8")
    (OUT_DIR / "SIGNAL_EVOLUTION_REPORT.md").write_text(report, encoding="utf-8")
    paths["report_md"] = str(REPORT_MD)

    decay = format_decay_md(result)
    DECAY_MD.write_text(decay, encoding="utf-8")
    (OUT_DIR / "SIGNAL_DECAY.md").write_text(decay, encoding="utf-8")
    paths["decay_md"] = str(DECAY_MD)

    drift = format_drift_md(result)
    DRIFT_MD.write_text(drift, encoding="utf-8")
    (OUT_DIR / "FEATURE_DRIFT.md").write_text(drift, encoding="utf-8")
    paths["drift_md"] = str(DRIFT_MD)

    payloads = {
        "ranked.json": result.get("ranked") or [],
        "strongest.json": result.get("strongest") or [],
        "weakest.json": result.get("weakest") or [],
        "drift_stats.json": result.get("drift_stats") or {},
        "lifecycle_counts.json": result.get("lifecycle_counts") or {},
        "promotion.json": result.get("promotion") or {},
        "summary.json": {
            "n_signals": result.get("n_signals"),
            "n_occurrences": result.get("n_occurrences"),
            "mean_update_ms": result.get("mean_update_ms"),
            "elapsed_sec": result.get("elapsed_sec"),
            "promotion_status": (result.get("promotion") or {}).get("status"),
        },
    }
    for name, payload in payloads.items():
        p = OUT_DIR / name
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[name] = str(p)
    return paths


__all__ = [
    "OUT_DIR",
    "format_decay_md",
    "format_drift_md",
    "format_evolution_report",
    "write_artifacts",
]
