"""Terminal + file reports for Market Timeline Intelligence V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPORT_MD = BASE_DIR / "MARKET_TIMELINE_REPORT.md"
REPORT_JSON = BASE_DIR / "MARKET_TIMELINE.json"
OUT_DIR = BASE_DIR / "reports" / "research" / "market_timeline_v1"


def _pf(v: Any) -> str:
    if v is None:
        return "inf"
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v)


def format_terminal(result: dict[str, Any]) -> str:
    """One-page MARKET TIMELINE SUMMARY."""
    sim = result.get("similarity") or {}
    top = result.get("top_chain") or {}
    lines = [
        "MARKET TIMELINE SUMMARY",
        "",
        "Corpus",
        f"  {result.get('n_timelines') or result.get('n_trades') or 0}",
        "",
        "Clusters",
        f"  {result.get('n_clusters') or len(result.get('clusters') or [])}",
        "",
        "Timeline chains",
        f"  {result.get('timeline_chains') or result.get('n_chains') or 0}",
        "",
        "Profitable chains",
        f"  {result.get('n_profitable_chains') or 0}",
        "",
        "READY chains",
        f"  {result.get('n_ready_chains') or 0}",
        "",
        "Top chain",
        f"  #{top.get('id') or 'n/a'}",
        "",
        "WR",
        f"  {top.get('wr')}%",
        "",
        "PF",
        f"  {_pf(top.get('pf'))}",
        "",
        "EV",
        f"  {top.get('ev')}",
        "",
        "Confidence",
        f"  {top.get('confidence')}",
        "",
        "Common duration",
        f"  {top.get('common_duration') or 'n/a'}",
        "",
        "Current market",
        "",
        "Similarity",
        f"  {sim.get('similarity_pct')}%",
        "",
        "Closest chain",
        f"  #{sim.get('closest_chain') or 'n/a'}",
        "",
        "Recommendation",
        f"  {sim.get('recommendation') or 'RESEARCH ONLY'}",
        "",
        f"elapsed={result.get('elapsed_sec')}s research_only=true",
    ]
    return "\n".join(lines)


def format_report(result: dict[str, Any]) -> str:
    sim = result.get("similarity") or {}
    top = result.get("top_chain") or {}
    lines = [
        "# MARKET_TIMELINE_REPORT",
        "",
        "_Market Timeline Intelligence Engine V1 — research only._",
        "",
        f"- corpus timelines: **{result.get('n_timelines')}** / trades: **{result.get('n_trades')}**",
        f"- clusters: **{result.get('n_clusters')}**",
        f"- chain signatures: **{result.get('timeline_chains')}** mined: **{result.get('n_chains')}**",
        f"- profitable: **{result.get('n_profitable_chains')}** READY: **{result.get('n_ready_chains')}**",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        "",
        "## Top chain",
        "",
        f"- id: **#{top.get('id')}**",
        f"- path: `{top.get('chain')}`",
        f"- n={top.get('n')} WR={top.get('wr')}% PF={_pf(top.get('pf'))} EV={top.get('ev')} conf={top.get('confidence')}",
        f"- common duration: `{top.get('common_duration')}`",
        "",
        "## Current market",
        "",
        f"- Similarity: **{sim.get('similarity_pct')}%**",
        f"- Closest chain: **#{sim.get('closest_chain')}**",
        f"- Hist WR/PF/EV: **{sim.get('historical_wr')}** / **{sim.get('historical_pf')}** / **{sim.get('historical_ev')}**",
        f"- Recommendation: **{sim.get('recommendation') or 'RESEARCH ONLY'}**",
        "",
        "## Clusters (top 10)",
        "",
    ]
    for c in (result.get("clusters") or [])[:10]:
        lines.append(
            f"- `{c.get('name')}` n={c.get('n')} WR={c.get('wr')} PF={_pf(c.get('pf'))} EV={c.get('ev')}"
        )
    lines.extend(["", "## Top profitable chains", ""])
    for c in (result.get("top_chains") or [])[:15]:
        lines.append(
            f"- #{c.get('id')} n={c.get('n')} WR={c.get('wr')} PF={_pf(c.get('pf'))} "
            f"ready={c.get('ready')} `{c.get('chain')}`"
        )
    lines.extend(["", "_Gate / Strategy / Paper / Optimizer / Execution / Brain unchanged._", ""])
    return "\n".join(lines[:200])


def write_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = format_report(result)
    payload = {
        "ok": result.get("ok"),
        "n_trades": result.get("n_trades"),
        "n_timelines": result.get("n_timelines"),
        "elapsed_sec": result.get("elapsed_sec"),
        "n_clusters": result.get("n_clusters"),
        "timeline_chains": result.get("timeline_chains"),
        "n_chains": result.get("n_chains"),
        "n_profitable_chains": result.get("n_profitable_chains"),
        "n_ready_chains": result.get("n_ready_chains"),
        "top_chain": result.get("top_chain"),
        "top_chains": result.get("top_chains"),
        "clusters": result.get("clusters"),
        "similarity": result.get("similarity"),
        "current_market": result.get("current_market"),
        "research_only": True,
    }
    REPORT_MD.write_text(md, encoding="utf-8")
    REPORT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (OUT_DIR / "MARKET_TIMELINE_REPORT.md").write_text(md, encoding="utf-8")
    (OUT_DIR / "MARKET_TIMELINE.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    return {
        "report_md": str(REPORT_MD),
        "report_json": str(REPORT_JSON),
        "out_dir": str(OUT_DIR),
        "report_text": md,
    }


__all__ = ["format_report", "format_terminal", "write_artifacts"]
