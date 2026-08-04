"""Reports for Decision Error Learning Engine V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "decision_error_learning_v1"


def _pf(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def format_terminal(result: dict[str, Any]) -> str:
    cm = result.get("confusion") or {}
    rank = result.get("module_ranking") or []
    worst = (result.get("largest_error_source") or (rank[0] if rank else {}))
    best = result.get("best_module")
    if not best:
        active = [
            r for r in rank
            if int(r.get("false_reject_n") or 0) + int(r.get("false_accept_n") or 0) > 0
        ]
        best = min(active or rank, key=lambda r: float(r.get("error_score") or 0)) if rank else {}
    lines = [
        "DECISION ERROR LEARNING ENGINE V1",
        "",
        f"n={result.get('n')} elapsed={result.get('elapsed_sec')}s",
        "",
        "Confusion",
        f"  TP={cm.get('TP')} FP={cm.get('FP')} TN={cm.get('TN')} FN={cm.get('FN')}",
        "",
        "Largest error source",
        f"  {worst.get('module')}  FR%={worst.get('false_reject_pct')} "
        f"recovered_EV={worst.get('recovered_ev')} total={worst.get('recovered_total_pnl')}",
        "",
        "Best module",
        f"  {best.get('module')}  score={best.get('error_score')}",
        "",
        "Suggestions",
    ]
    for s in (result.get("suggestions") or [])[:8]:
        lines.append(
            f"  {s.get('module')}: {s.get('verdict')} "
            f"(FR%={s.get('false_reject_pct')} FA%={s.get('false_accept_pct')})"
        )
    lines.extend(["", "research_only=true"])
    return "\n".join(lines)


def format_error_report(result: dict[str, Any]) -> str:
    return "\n".join([
        "# DECISION_ERROR_REPORT",
        "",
        "_Decision Error Learning Engine V1 — research only._",
        "",
        f"- n: {result.get('n')}",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- confusion: `{result.get('confusion')}`",
        "",
        "## Largest error source",
        f"```\n{json.dumps((result.get('module_ranking') or [None])[0], indent=2, default=str)}\n```",
        "",
        "## Suggestions",
        f"```\n{json.dumps(result.get('suggestions'), indent=2, default=str)}\n```",
        "",
    ])


def format_fr_library(result: dict[str, Any]) -> str:
    lines = ["# FALSE_REJECT_LIBRARY", ""]
    for p in (result.get("false_reject_patterns") or [])[:40]:
        lines.append(
            f"- n={p.get('n')} total={p.get('total_pnl')} EV={p.get('recovered_ev')} "
            f"mod={p.get('primary_module')} — `{p.get('pattern')}`"
        )
    lines.append("")
    return "\n".join(lines)


def format_fa_library(result: dict[str, Any]) -> str:
    lines = ["# FALSE_ACCEPT_LIBRARY", ""]
    for p in (result.get("false_accept_patterns") or [])[:40]:
        lines.append(
            f"- n={p.get('n')} total={p.get('total_pnl')} mean={p.get('mean_pnl')} "
            f"mod={p.get('primary_module')} — `{p.get('pattern')}`"
        )
    lines.append("")
    return "\n".join(lines)


def format_ranking(result: dict[str, Any]) -> str:
    lines = [
        "# MODULE_ERROR_RANKING",
        "",
        "| module | FR% | FA% | recovered EV | recovered WR | recovered PF | score |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in result.get("module_ranking") or []:
        lines.append(
            f"| {r.get('module')} | {r.get('false_reject_pct')} | {r.get('false_accept_pct')} | "
            f"{r.get('recovered_ev')} | {r.get('recovered_wr')} | {_pf(r.get('recovered_pf'))} | "
            f"{r.get('error_score')} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_recovered_ev(result: dict[str, Any]) -> str:
    lines = ["# RECOVERED_EV", "", "If primary error module were ignored on False Rejects:", ""]
    for r in result.get("module_ranking") or []:
        lines.append(
            f"- **{r.get('module')}**: recovered_EV={r.get('recovered_ev')} "
            f"total_pnl={r.get('recovered_total_pnl')} n_FR={r.get('false_reject_n')}"
        )
    lines.append("")
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "DECISION_ERROR_REPORT.md": format_error_report(result),
        "FALSE_REJECT_LIBRARY.md": format_fr_library(result),
        "FALSE_ACCEPT_LIBRARY.md": format_fa_library(result),
        "MODULE_ERROR_RANKING.md": format_ranking(result),
        "RECOVERED_EV.md": format_recovered_ev(result),
    }
    paths: dict[str, str] = {}
    for name, text in files.items():
        root = BASE_DIR / name
        out = OUT_DIR / name
        root.write_text(text, encoding="utf-8")
        out.write_text(text, encoding="utf-8")
        paths[name] = str(root)
        paths[f"out_{name}"] = str(out)
    slim = {k: v for k, v in result.items() if k not in ("terminal", "records")}
    jp = OUT_DIR / "decision_error_learning.json"
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


__all__ = ["format_terminal", "write_artifacts"]
