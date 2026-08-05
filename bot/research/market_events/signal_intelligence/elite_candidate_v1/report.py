"""Reports for Elite Candidate Engine V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "elite_candidate_v1"


def format_terminal(result: dict[str, Any]) -> str:
    cats = result.get("categories") or {}
    lines = [
        "ELITE CANDIDATE ENGINE V1",
        "",
        f"scored={result.get('n_scored')} stored={result.get('n_stored')} "
        f"elapsed={result.get('elapsed_sec')}s",
        f"categories ELITE={cats.get('ELITE', 0)} A+={cats.get('A+', 0)} "
        f"A={cats.get('A', 0)} B={cats.get('B', 0)} IGNORE={cats.get('IGNORE', 0)}",
        f"learn_events={result.get('learn_events')}",
        "",
        "TODAY ELITE / TOP",
    ]
    for i, c in enumerate((result.get("candidates") or [])[:8], 1):
        lines.append(
            f"  {i}. {c.get('symbol')} {c.get('direction')}  "
            f"Score {c.get('score')} {c.get('category')}  "
            f"WR {c.get('historical_wr')} PF {c.get('historical_pf')} EV {c.get('historical_ev')}"
        )
        mods = c.get("supporting_modules") or []
        if mods:
            lines.append(f"     Reason {' '.join(str(m).title() for m in mods[:5])}")
    lines.extend(["", "research_only=true"])
    return "\n".join(lines)


def format_elite_report(result: dict[str, Any]) -> str:
    lines = [
        "# ELITE_CANDIDATE_REPORT",
        "",
        "_Elite Candidate Engine V1 — research only._",
        "",
        f"- scored: {result.get('n_scored')}",
        f"- stored (ELITE/A+/A): {result.get('n_stored')}",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- categories: `{result.get('categories')}`",
        f"- learn_events: {result.get('learn_events')}",
        "",
        "## Top candidates",
        "",
    ]
    for i, c in enumerate((result.get("candidates") or [])[:30], 1):
        lines.append(
            f"{i}. **{c.get('symbol')} {c.get('direction')}** Score `{c.get('score')}` "
            f"`{c.get('category')}` WR={c.get('historical_wr')} "
            f"PF={c.get('historical_pf')} EV={c.get('historical_ev')}"
        )
        lines.append(f"   - WHY: {'; '.join(c.get('why') or [])}")
        lines.append(f"   - WHY NOT: {'; '.join(c.get('why_not') or [])}")
    lines.append("")
    return "\n".join(lines)


def format_today_elite(candidates: list[dict[str, Any]]) -> str:
    lines = ["TODAY ELITE", ""]
    if not candidates:
        lines.append("(none)")
        lines.append("")
        return "\n".join(lines)
    for i, c in enumerate(candidates[:15], 1):
        lines.extend([
            f"{i}",
            f"{c.get('symbol')} {c.get('direction')}",
            f"Score {c.get('score')}",
            f"WR {c.get('historical_wr')}%",
            f"PF {c.get('historical_pf')}",
            f"EV {c.get('expected_ev') or c.get('historical_ev')}",
            "",
            "Reason",
        ])
        for m in c.get("supporting_modules") or []:
            lines.append(str(m).title())
        if c.get("supporting_modules"):
            lines.append("ALL PASS")
        lines.append("")
        lines.append("----------------")
        lines.append("")
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_md = format_elite_report(result)
    today_md = format_today_elite(result.get("candidates") or [])
    files = {
        "ELITE_CANDIDATE_REPORT.md": report_md,
        "TODAY_ELITE.md": today_md,
    }
    paths: dict[str, str] = {}
    for name, text in files.items():
        root = BASE_DIR / name
        out = OUT_DIR / name
        root.write_text(text, encoding="utf-8")
        out.write_text(text, encoding="utf-8")
        paths[name] = str(root)
        paths[f"out_{name}"] = str(out)
    slim = {k: v for k, v in result.items() if k not in ("terminal",)}
    # trim candidate payloads in json
    slim_c = []
    for c in (result.get("candidates") or [])[:100]:
        slim_c.append({
            k: c.get(k)
            for k in (
                "trade_id", "symbol", "direction", "category", "score", "base_score",
                "learned_score", "historical_wr", "historical_pf", "historical_ev",
                "supporting_modules", "rejecting_modules", "why", "why_not",
                "current_regime", "current_transition",
            )
        })
    slim["candidates"] = slim_c
    jp = OUT_DIR / "elite_candidates.json"
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


__all__ = [
    "format_elite_report",
    "format_terminal",
    "format_today_elite",
    "write_artifacts",
]
