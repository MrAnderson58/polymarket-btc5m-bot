"""TRADING_DNA_REPORT.md + morning summary (plain terminal)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPORT_MD = BASE_DIR / "TRADING_DNA_REPORT.md"
OUT_DIR = BASE_DIR / "reports" / "research" / "trading_dna_v1"


def _pf(v: Any) -> str:
    if v is None:
        return "inf"
    return f"{float(v):.2f}"


def format_morning_summary(result: dict[str, Any]) -> str:
    """One-screen plain text. No markdown. No JSON."""
    lines = [
        "TRADING DNA — MORNING SUMMARY",
        f"corpus={result.get('n_trades')}  runtime={result.get('elapsed_sec')}s  candle_hit={((result.get('feature_stats') or {}).get('candle_hit_rate'))}",
        "",
        "TOP 10 SETUPS",
    ]
    for i, s in enumerate((result.get("profitable") or [])[:10], 1):
        lines.append(
            f"{i:2d}. {s.get('setup')}  |  PF {_pf(s.get('pf'))}  EV {s.get('ev')}  n={s.get('n')}  conf={s.get('confidence')}"
        )
    lines.extend(["", "TOP 10 FORBIDDEN (BLOCK)"])
    forb = result.get("forbidden") or []
    if not forb:
        lines.append("  (none under PF<=0.70 with enough trades)")
    for i, s in enumerate(forb[:10], 1):
        lines.append(
            f"{i:2d}. BLOCK  {s.get('setup')}  |  PF {_pf(s.get('pf'))}  EV {s.get('ev')}  n={s.get('n')}"
        )
    lines.append("")
    mini = (result.get("minimal_rules") or [])[:3]
    if mini:
        lines.append("MINIMAL RULES")
        for m in mini:
            rules = " + ".join(m.get("rules") or [])
            lines.append(
                f"  {rules}  →  PF {_pf(m.get('pf'))}  EV {m.get('ev')}  n={m.get('n')}"
            )
    return "\n".join(lines)


def format_report(result: dict[str, Any]) -> str:
    """Compact TRADING_DNA_REPORT (~10 pages max)."""
    lines = [
        "# TRADING_DNA_REPORT",
        "",
        "_Trading DNA Discovery V1 — research only. Actual setups, not modules._",
        "",
        f"- closed trades: **{result.get('n_trades')}**",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- candle hit rate: **{((result.get('feature_stats') or {}).get('candle_hit_rate'))}**",
        "",
        "## 1. Segment DNA",
        "",
        "| segment | n | PF | EV | WR |",
        "|---|---:|---:|---:|---:|",
    ]
    for p in result.get("profiles") or []:
        m = p.get("metrics") or {}
        lines.append(
            f"| {p.get('segment')} | {m.get('n')} | {_pf(m.get('pf'))} | {m.get('ev')} | {m.get('wr')} |"
        )

    cf = result.get("common_factors") or {}
    lines.extend(["", "## 2. TOP10 vs BOTTOM10 factors", ""])
    for d in (cf.get("numeric_deltas") or [])[:8]:
        lines.append(
            f"- `{d.get('feature')}`: top10={d.get('top10_median')} bottom10={d.get('bottom10_median')} Δ={d.get('delta')}"
        )
    for c in (cf.get("categorical_modes") or [])[:6]:
        lines.append(
            f"- `{c.get('feature')}`: top={c.get('top10_mode')} ({c.get('top10_share')}) "
            f"bottom={c.get('bottom10_mode')} ({c.get('bottom10_share')})"
        )

    lines.extend([
        "",
        "## 3. TOP profitable setups (100)",
        "",
        "| # | setup | PF | EV | n | conf |",
        "|---:|---|---:|---:|---:|---:|",
    ])
    for i, s in enumerate((result.get("profitable") or [])[:100], 1):
        lines.append(
            f"| {i} | {s.get('setup')} | {_pf(s.get('pf'))} | {s.get('ev')} | {s.get('n')} | {s.get('confidence')} |"
        )

    lines.extend([
        "",
        "## 4. TOP losing setups (100)",
        "",
        "| # | setup | PF | EV | n |",
        "|---:|---|---:|---:|---:|",
    ])
    for i, s in enumerate((result.get("losing") or [])[:100], 1):
        lines.append(
            f"| {i} | {s.get('setup')} | {_pf(s.get('pf'))} | {s.get('ev')} | {s.get('n')} |"
        )

    lines.extend(["", "## 5. Minimal rule sets", ""])
    for m in (result.get("minimal_rules") or [])[:12]:
        rules = " + ".join(m.get("rules") or [])
        lines.append(
            f"- `{rules}` → PF {_pf(m.get('pf'))}  EV {m.get('ev')}  n={m.get('n')}  conf={m.get('confidence')}"
        )

    lines.extend(["", "## 6. Forbidden setups (BLOCK)", ""])
    for s in (result.get("forbidden") or [])[:20]:
        lines.append(
            f"- BLOCK `{s.get('setup')}` → PF {_pf(s.get('pf'))}  EV {s.get('ev')}  n={s.get('n')}"
        )

    lines.extend(["", "## 7. Top 20 coins", ""])
    for c in result.get("coins") or []:
        best = "; ".join(
            f"{b.get('condition')} PF={_pf(b.get('pf'))} n={b.get('n')}"
            for b in (c.get("best") or [])[:2]
        ) or "-"
        worst = "; ".join(
            f"{w.get('condition')} PF={_pf(w.get('pf'))} n={w.get('n')}"
            for w in (c.get("worst") or [])[:2]
        ) or "-"
        lines.append(
            f"- **{c.get('symbol')}** n={c.get('n')} PF={_pf(c.get('pf'))} EV={c.get('ev')}  "
            f"| best: {best}  | worst: {worst}"
        )

    lines.extend([
        "",
        "## Integrity",
        "",
        f"- research_only: {result.get('research_only')}",
        f"- paper_unchanged: {result.get('paper_unchanged')}",
        f"- execution_unchanged: {result.get('execution_unchanged')}",
        f"- strategy_unchanged: {result.get('strategy_unchanged')}",
        f"- gate_unchanged: {result.get('gate_unchanged')}",
        f"- optimizer_unchanged: {result.get('optimizer_unchanged')}",
        f"- brain_unchanged: {result.get('brain_unchanged')}",
        "",
    ])
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    md = format_report(result)
    REPORT_MD.write_text(md, encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "TRADING_DNA_REPORT.md").write_text(md, encoding="utf-8")
    return {
        "TRADING_DNA_REPORT.md": str(REPORT_MD),
        "out/TRADING_DNA_REPORT.md": str(OUT_DIR / "TRADING_DNA_REPORT.md"),
    }


__all__ = [
    "format_morning_summary",
    "format_report",
    "write_artifacts",
]
