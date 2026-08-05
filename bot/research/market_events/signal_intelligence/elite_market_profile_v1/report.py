"""Reports for Elite Market Profile V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "elite_market_profile_v1"


def format_terminal(result: dict[str, Any]) -> str:
    lines = [
        "ELITE MARKET PROFILE V1",
        "",
        f"elite_n={result.get('elite_n')} ignore_n={result.get('ignore_n')} "
        f"elapsed={result.get('elapsed_sec')}s",
        "",
        "Coins",
    ]
    for r in (result.get("coins") or [])[:8]:
        lines.append(f"  {r.get('key')} n={r.get('n')} pct={r.get('pct')}% WR={r.get('wr')} PF={r.get('pf')}")
    lines.append("")
    lines.append("Direction")
    for r in (result.get("directions") or [])[:4]:
        lines.append(f"  {r.get('key')} n={r.get('n')} pct={r.get('pct')}% WR={r.get('wr')}")
    lines.append("")
    lines.append("Sessions")
    for r in (result.get("sessions") or [])[:6]:
        lines.append(f"  {r.get('key')} n={r.get('n')} pct={r.get('pct')}%")
    lines.extend(["", "TOP combos"])
    for c in (result.get("combos") or [])[:5]:
        lines.append(
            f"  #{c.get('rank')} n={c.get('n')} WR={c.get('wr')} PF={c.get('pf')} — {c.get('pattern')}"
        )
    lines.extend(["", "ELITE vs IGNORE (top delta)"])
    for v in (result.get("compare") or [])[:8]:
        lines.append(
            f"  {v.get('feature')}: ELITE {v.get('elite_pct')}% | IGNORE {v.get('ignore_pct')}% "
            f"(Δ{v.get('delta_pct')})"
        )
    lines.extend(["", "research_only=true"])
    return "\n".join(lines)


def format_profile_md(result: dict[str, Any]) -> str:
    lines = [
        "# ELITE_MARKET_PROFILE",
        "",
        "_Statistical portrait of ELITE / A+ / A candidates only._",
        "",
        f"- elite_n: {result.get('elite_n')}",
        f"- ignore_n: {result.get('ignore_n')}",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        "",
        "## Coins",
        "",
    ]
    for r in result.get("coins") or []:
        lines.append(f"- {r.get('key')}: n={r.get('n')} ({r.get('pct')}%) WR={r.get('wr')} PF={r.get('pf')} EV={r.get('ev')}")
    lines.extend(["", "## Direction", ""])
    for r in result.get("directions") or []:
        lines.append(f"- {r.get('key')}: n={r.get('n')} ({r.get('pct')}%) WR={r.get('wr')}")
    lines.extend(["", "## Time / Session", ""])
    for r in (result.get("hours") or [])[:24]:
        lines.append(f"- {r.get('key')}: n={r.get('n')} ({r.get('pct')}%)")
    for r in result.get("weekdays") or []:
        lines.append(f"- {r.get('key')}: n={r.get('n')} ({r.get('pct')}%)")
    for r in result.get("sessions") or []:
        lines.append(f"- {r.get('key')}: n={r.get('n')} ({r.get('pct')}%)")
    lines.extend(["", "## Market features", ""])
    for r in (result.get("market") or [])[:40]:
        lines.append(f"- {r.get('key')}: n={r.get('n')} ({r.get('pct')}%) WR={r.get('wr')} PF={r.get('pf')}")
    lines.append("")
    return "\n".join(lines)


def format_combos_md(result: dict[str, Any]) -> str:
    lines = ["# ELITE_COMBOS", "", "TOP-100 characteristic ELITE combinations.", ""]
    for c in result.get("combos") or []:
        lines.append(
            f"{c.get('rank')}. n={c.get('n')} WR={c.get('wr')}% PF={c.get('pf')} EV={c.get('ev')} — "
            f"`{c.get('pattern')}`"
        )
    lines.append("")
    return "\n".join(lines)


def format_compare_md(result: dict[str, Any]) -> str:
    lines = [
        "# ELITE_VS_IGNORE",
        "",
        "| Feature | ELITE | IGNORE | Δ |",
        "|---|---:|---:|---:|",
    ]
    for v in result.get("compare") or []:
        lines.append(
            f"| {v.get('feature')} | {v.get('elite_pct')}% | {v.get('ignore_pct')}% | {v.get('delta_pct')} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "ELITE_MARKET_PROFILE.md": format_profile_md(result),
        "ELITE_COMBOS.md": format_combos_md(result),
        "ELITE_VS_IGNORE.md": format_compare_md(result),
    }
    paths: dict[str, str] = {}
    for name, text in files.items():
        root = BASE_DIR / name
        out = OUT_DIR / name
        root.write_text(text, encoding="utf-8")
        out.write_text(text, encoding="utf-8")
        paths[name] = str(root)
        paths[f"out_{name}"] = str(out)
    slim = {
        k: result.get(k)
        for k in (
            "ok", "elite_n", "ignore_n", "elapsed_sec", "coins", "directions",
            "sessions", "weekdays", "hours", "market", "combos", "compare", "stored",
        )
    }
    jp = OUT_DIR / "elite_market_profile.json"
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


__all__ = [
    "format_combos_md",
    "format_compare_md",
    "format_profile_md",
    "format_terminal",
    "write_artifacts",
]
