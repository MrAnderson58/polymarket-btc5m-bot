"""Reports for Market Regime Transition Engine V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

OUT_DIR = BASE_DIR / "reports" / "research" / "market_regime_transition_v1"


def _pf(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def format_terminal(result: dict[str, Any]) -> str:
    pred = result.get("prediction") or {}
    rec = result.get("recommendation") or {}
    top = (result.get("transitions") or {}).get("profitable") or []
    lines = [
        "MARKET REGIME TRANSITION ENGINE V1",
        "",
        f"corpus_n={result.get('n')} elapsed={result.get('elapsed_sec')}s",
        "",
        "Current",
        f"  {pred.get('current') or rec.get('current_regime') or '—'}",
        "",
        "Prediction",
    ]
    for p in (pred.get("predictions") or [])[:4]:
        lines.append(f"  {p.get('prob_pct')}%  {p.get('state')}")
    if not pred.get("predictions"):
        lines.append("  —")
    lines.extend([
        "",
        "Recommendation",
        f"  bias={rec.get('recommended_bias')} conf={rec.get('confidence')}",
        f"  hist WR={rec.get('historical_wr')} PF={rec.get('historical_pf')} EV={rec.get('historical_ev')}",
        f"  {rec.get('recommendation') or 'RESEARCH ONLY'}",
        "",
        "Top transitions",
    ])
    for t in top[:8]:
        ready = "READY" if t.get("ready") else "cand"
        lines.append(
            f"  [{ready}] {t.get('transition_key')} n={t.get('n')} "
            f"WR={t.get('wr')} EV={t.get('ev')} PF={_pf(t.get('pf'))}"
        )
    lines.extend(["", f"research_only=true"])
    return "\n".join(lines)


def format_regime_report(result: dict[str, Any]) -> str:
    return "\n".join([
        "# REGIME_TRANSITION_REPORT",
        "",
        "_Market Regime Transition Engine V1 — research only._",
        "",
        f"- n: {result.get('n')}",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- markov transitions: {(result.get('markov') or {}).get('n_transitions')}",
        "",
        "## Current / Prediction",
        f"```\n{json.dumps(result.get('prediction'), indent=2)}\n```",
        "",
        "## Recommendation",
        f"```\n{json.dumps(result.get('recommendation'), indent=2)}\n```",
        "",
        "## Top profitable",
        "",
        *[
            f"- `{t.get('transition_key')}` n={t.get('n')} WR={t.get('wr')} EV={t.get('ev')} "
            f"ready={t.get('ready')} p={t.get('perm_p')}"
            for t in ((result.get("transitions") or {}).get("profitable") or [])[:25]
        ],
        "",
        "## Top dangerous",
        "",
        *[
            f"- `{t.get('transition_key')}` n={t.get('n')} WR={t.get('wr')} EV={t.get('ev')}"
            for t in ((result.get("transitions") or {}).get("dangerous") or [])[:15]
        ],
        "",
    ])


def format_library_md(result: dict[str, Any]) -> str:
    stored = result.get("stored") or {}
    return "\n".join([
        "# TRANSITION_LIBRARY",
        "",
        f"- transitions persisted: {stored.get('transitions')}",
        f"- edges: {stored.get('edges')}",
        f"- sequences: {stored.get('sequences')}",
        "",
        "Tables: `market_regime_transitions_v1`, `transition_edges_v1`, `transition_sequences_v1`",
        "",
    ])


def format_markov_md(result: dict[str, Any]) -> str:
    m = result.get("markov") or {}
    states = m.get("states") or []
    matrix = m.get("matrix") or {}
    lines = ["# MARKOV_MATRIX", "", "| from \\ to | " + " | ".join(states) + " |",
             "|---|" + "|".join(["---"] * len(states)) + "|"]
    for a in states:
        row = [a] + [f"{(matrix.get(a) or {}).get(b, 0):.3f}" for b in states]
        lines.append("| " + " | ".join(row) + " |")
    lines.extend(["", f"current={m.get('current_state')}", ""])
    return "\n".join(lines)


def format_heatmap_md(result: dict[str, Any]) -> str:
    m = result.get("markov") or {}
    lines = ["# TRANSITION_HEATMAP", "", "Expected EV by edge (from→to):", ""]
    for e in (m.get("edges") or [])[:40]:
        lines.append(
            f"- {e.get('from_state')}→{e.get('to_state')} "
            f"p={e.get('prob')} EV={e.get('expected_ev')} WR={e.get('expected_wr')}"
        )
    lines.append("")
    return "\n".join(lines)


def format_sequence_md(result: dict[str, Any]) -> str:
    lines = ["# SEQUENCE_REPORT", "", "Top transition chains:", ""]
    for s in (result.get("sequences") or [])[:40]:
        lines.append(
            f"- n={s.get('n')} WR={s.get('wr')} EV={s.get('ev')} PF={_pf(s.get('pf'))} "
            f"ready={s.get('ready')} — {s.get('chain')}"
        )
    lines.append("")
    return "\n".join(lines)


def format_current_md(result: dict[str, Any]) -> str:
    payload = {
        "prediction": result.get("prediction"),
        "recommendation": result.get("recommendation"),
    }
    return (
        "# CURRENT_STATE_REPORT\n\n"
        f"```\n{json.dumps(payload, indent=2)}\n```\n"
    )


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "REGIME_TRANSITION_REPORT.md": format_regime_report(result),
        "TRANSITION_LIBRARY.md": format_library_md(result),
        "MARKOV_MATRIX.md": format_markov_md(result),
        "TRANSITION_HEATMAP.md": format_heatmap_md(result),
        "SEQUENCE_REPORT.md": format_sequence_md(result),
        "CURRENT_STATE_REPORT.md": format_current_md(result),
    }
    paths: dict[str, str] = {}
    for name, text in files.items():
        root = BASE_DIR / name
        out = OUT_DIR / name
        root.write_text(text, encoding="utf-8")
        out.write_text(text, encoding="utf-8")
        paths[name] = str(root)
        paths[f"out_{name}"] = str(out)
    slim = {k: v for k, v in result.items() if k != "terminal"}
    jp = OUT_DIR / "regime_transition.json"
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


__all__ = [
    "format_terminal",
    "write_artifacts",
]
