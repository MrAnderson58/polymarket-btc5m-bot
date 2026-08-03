"""Terminal + file reports for Market Decision Engine V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    format_decision,
)

REPORT_MD = BASE_DIR / "MARKET_DECISION_REPORT.md"
REPORT_JSON = BASE_DIR / "MARKET_DECISION.json"
OUT_DIR = BASE_DIR / "reports" / "research" / "market_decision_v1"


def _pf(v: Any) -> str:
    if v is None:
        return "inf"
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def format_terminal(result: dict[str, Any]) -> str:
    """One-page Production vs Decision Engine summary."""
    prod = result.get("production") or {}
    eng = result.get("decision_engine") or {}
    sample = result.get("sample_decision") or {}
    lines = [
        "MARKET DECISION ENGINE V1",
        "",
        "Production",
        f"  {prod.get('n') or 0} trades",
        f"  PF {_pf(prod.get('pf'))}",
        f"  WR {prod.get('wr')}",
        f"  EV {prod.get('ev')}",
        f"  Sharpe {prod.get('sharpe')}",
        f"  MaxDD {prod.get('max_dd')}",
        "",
        "Decision Engine",
        f"  {eng.get('n') or 0} trades",
        f"  PF {_pf(eng.get('pf'))}",
        f"  WR {eng.get('wr')}",
        f"  EV {eng.get('ev')}",
        f"  Sharpe {eng.get('sharpe')}",
        f"  MaxDD {eng.get('max_dd')}",
        "",
        "Skipped",
        f"  {result.get('skipped') or 0}",
        "",
    ]
    if sample:
        lines.append("Latest probe")
        lines.append("")
        lines.append(format_decision(sample))
        lines.append("")
    lines.append(f"elapsed={result.get('elapsed_sec')}s research_only=true")
    return "\n".join(lines)


def format_report(result: dict[str, Any]) -> str:
    prod = result.get("production") or {}
    eng = result.get("decision_engine") or {}
    sample = result.get("sample_decision") or {}
    ctx = result.get("context_stats") or {}
    lines = [
        "# MARKET_DECISION_REPORT",
        "",
        "_Market Decision Engine V1 — research only. No new features; composes existing engines._",
        "",
        f"- build: fp={ctx.get('n_fp')} tl={ctx.get('n_tl')} dna={ctx.get('n_dna')} "
        f"ready_rules={ctx.get('n_ready_rules')} edges={ctx.get('n_edges')} "
        f"replays={ctx.get('n_replays')} causality={ctx.get('n_causality')}",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        "",
        "## Production",
        "",
        f"- n={prod.get('n')} PF={_pf(prod.get('pf'))} WR={prod.get('wr')} EV={prod.get('ev')} "
        f"Sharpe={prod.get('sharpe')} MaxDD={prod.get('max_dd')}",
        "",
        "## Decision Engine filter",
        "",
        f"- n={eng.get('n')} PF={_pf(eng.get('pf'))} WR={eng.get('wr')} EV={eng.get('ev')} "
        f"Sharpe={eng.get('sharpe')} MaxDD={eng.get('max_dd')}",
        f"- skipped={result.get('skipped')} long={eng.get('long')} short={eng.get('short')}",
        "",
        "## Latest probe",
        "",
        "```",
        format_decision(sample) if sample else "n/a",
        "```",
        "",
        "_Gate / Strategy / Paper / Optimizer / Execution / Brain codepaths unchanged._",
        "",
    ]
    return "\n".join(lines[:200])


def write_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = format_report(result)
    payload = {
        "ok": result.get("ok"),
        "elapsed_sec": result.get("elapsed_sec"),
        "production": result.get("production"),
        "decision_engine": result.get("decision_engine"),
        "skipped": result.get("skipped"),
        "n_production": result.get("n_production"),
        "n_kept": result.get("n_kept"),
        "sample_decision": result.get("sample_decision"),
        "context_stats": result.get("context_stats"),
        "research_only": True,
    }
    REPORT_MD.write_text(md, encoding="utf-8")
    REPORT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (OUT_DIR / "MARKET_DECISION_REPORT.md").write_text(md, encoding="utf-8")
    (OUT_DIR / "MARKET_DECISION.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    return {
        "report_md": str(REPORT_MD),
        "report_json": str(REPORT_JSON),
        "out_dir": str(OUT_DIR),
        "report_text": md,
    }


__all__ = ["format_report", "format_terminal", "write_artifacts"]
