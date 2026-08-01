"""Reports for Shadow Live Evaluation V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "shadow_live_v1"
REPORT_MD = BASE_DIR / "SHADOW_LIVE_REPORT.md"
SCORECARD_MD = BASE_DIR / "BRAIN_SCORECARD.md"
PROMOTION_MD = BASE_DIR / "PROMOTION_STATUS.md"


def format_shadow_report(result: dict[str, Any]) -> str:
    rolling = result.get("rolling") or {}
    all_m = rolling.get("all") or {}
    lines = [
        "# SHADOW_LIVE_REPORT",
        "",
        "_Shadow Live Evaluation V1 — research-only. No trade execution. "
        "Does not modify Paper / Execution / Gate / Strategy / Optimizer._",
        "",
        "## Run",
        "",
        f"- candidates: **{result.get('n_candidates')}**",
        f"- shadow decisions: **{result.get('n_decisions')}**",
        f"- evaluated (closed): **{result.get('n_evaluated')}**",
        f"- runtime total: **{result.get('elapsed_sec')}s**",
        f"- mean decision latency: **{result.get('mean_decision_ms')} ms**",
        f"- p95 decision latency: **{result.get('p95_decision_ms')} ms**",
        f"- lake source: `{(result.get('load_stats') or {}).get('source')}`",
        "",
        "## Production vs Brain (all)",
        "",
        f"```json\n{json.dumps(all_m, indent=2, default=str)}\n```",
        "",
        "## Rolling windows",
        "",
    ]
    for key in ("last_50", "last_100", "last_500", "last_1000"):
        lines.append(f"### {key}")
        lines.append("")
        lines.append(f"```json\n{json.dumps(rolling.get(key) or {}, indent=2, default=str)}\n```")
        lines.append("")
    lines.extend([
        "## Confidence calibration curve",
        "",
        f"```json\n{json.dumps(result.get('confidence_curve') or [], indent=2)}\n```",
        "",
        "## Calibration summary",
        "",
        f"```json\n{json.dumps(result.get('calibration') or {}, indent=2)}\n```",
        "",
        "## Promotion",
        "",
        f"```json\n{json.dumps(result.get('promotion') or {}, indent=2)}\n```",
        "",
        "## Integrity",
        "",
        f"- no_execution: {result.get('no_execution')}",
        f"- gate_unchanged: {result.get('gate_unchanged')}",
        f"- strategy_unchanged: {result.get('strategy_unchanged')}",
        f"- paper_unchanged: {result.get('paper_unchanged')}",
        f"- execution_unchanged: {result.get('execution_unchanged')}",
        f"- optimizer_unchanged: {result.get('optimizer_unchanged')}",
        f"- research_only: {result.get('research_only')}",
        "",
    ])
    return "\n".join(lines)


def format_scorecard(result: dict[str, Any]) -> str:
    sc = result.get("scorecard") or {}
    pv = sc.get("production_vs_brain") or (result.get("rolling") or {}).get("all") or {}
    lines = [
        "# BRAIN_SCORECARD",
        "",
        "Shadow Live Evaluation — Brain vs Production.",
        "",
        "## Headline",
        "",
        f"- Brain hit rate: **{pv.get('brain_hit_rate')}**",
        f"- Production hit rate: **{pv.get('production_hit_rate')}**",
        f"- ΔWR: **{pv.get('delta_wr')}**",
        f"- ΔEV: **{pv.get('delta_ev')}**",
        f"- ΔPF: **{pv.get('delta_pf')}**",
        f"- False positives: **{pv.get('false_positives')}**",
        f"- False negatives: **{pv.get('false_negatives')}**",
        f"- Brain wins / Production wins / Ties: "
        f"**{pv.get('brain_wins')}** / **{pv.get('production_wins')}** / **{pv.get('ties')}**",
        "",
        "## Runtime",
        "",
        f"```json\n{json.dumps(sc.get('runtime') or {}, indent=2)}\n```",
        "",
        "## Rolling",
        "",
        f"```json\n{json.dumps(sc.get('rolling') or {}, indent=2, default=str)}\n```",
        "",
        "## Confidence reliability",
        "",
        f"```json\n{json.dumps(sc.get('calibration') or result.get('calibration') or {}, indent=2)}\n```",
        "",
        "## Confidence curve",
        "",
        "| confidence | n | actual WR | reliable |",
        "|---|---|---|---|",
    ]
    for c in (result.get("confidence_curve") or []):
        lines.append(
            f"| {c.get('confidence')} | {c.get('n')} | {c.get('actual_wr')} | {c.get('reliable')} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_promotion(result: dict[str, Any]) -> str:
    p = result.get("promotion") or {}
    lines = [
        "# PROMOTION_STATUS",
        "",
        "_Recommendation only. No automatic promotion._",
        "",
        f"## Status: **{p.get('status')}**",
        "",
        f"{p.get('recommendation')}",
        "",
        f"- ready_for_paper_ab: **{p.get('ready_for_paper_ab')}**",
        f"- auto_promotion: **{p.get('auto_promotion')}** (always false)",
        f"- evaluated n: **{p.get('n')}**",
        "",
        "## Reasons",
        "",
    ]
    for r in p.get("reasons") or []:
        lines.append(f"- {r}")
    lines.extend([
        "",
        "## Ladder",
        "",
        "1. NOT_READY",
        "2. PROMISING",
        "3. BEATS_PRODUCTION",
        "4. READY_FOR_PAPER_AB",
        "",
    ])
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    report = format_shadow_report(result)
    REPORT_MD.write_text(report, encoding="utf-8")
    (OUT_DIR / "SHADOW_LIVE_REPORT.md").write_text(report, encoding="utf-8")
    paths["report_md"] = str(REPORT_MD)

    score = format_scorecard(result)
    SCORECARD_MD.write_text(score, encoding="utf-8")
    (OUT_DIR / "BRAIN_SCORECARD.md").write_text(score, encoding="utf-8")
    paths["scorecard_md"] = str(SCORECARD_MD)

    promo = format_promotion(result)
    PROMOTION_MD.write_text(promo, encoding="utf-8")
    (OUT_DIR / "PROMOTION_STATUS.md").write_text(promo, encoding="utf-8")
    paths["promotion_md"] = str(PROMOTION_MD)

    payloads = {
        "rolling.json": result.get("rolling") or {},
        "confidence_curve.json": result.get("confidence_curve") or [],
        "calibration.json": result.get("calibration") or {},
        "promotion.json": result.get("promotion") or {},
        "scorecard.json": result.get("scorecard") or {},
        "summary.json": {
            "n_candidates": result.get("n_candidates"),
            "n_decisions": result.get("n_decisions"),
            "n_evaluated": result.get("n_evaluated"),
            "mean_decision_ms": result.get("mean_decision_ms"),
            "promotion_status": (result.get("promotion") or {}).get("status"),
            "elapsed_sec": result.get("elapsed_sec"),
        },
    }
    for name, payload in payloads.items():
        p = OUT_DIR / name
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[name] = str(p)
    return paths


__all__ = [
    "OUT_DIR",
    "format_promotion",
    "format_scorecard",
    "format_shadow_report",
    "write_artifacts",
]
