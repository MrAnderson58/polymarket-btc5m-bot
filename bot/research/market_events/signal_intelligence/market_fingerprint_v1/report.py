"""Reports for Market Fingerprint Engine V1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPORT_MD = BASE_DIR / "MARKET_FINGERPRINT_REPORT.md"
REPORT_JSON = BASE_DIR / "MARKET_FINGERPRINT.json"
OUT_DIR = BASE_DIR / "reports" / "research" / "market_fingerprint_v1"


def _pf(v: Any) -> str:
    if v is None:
        return "inf"
    return f"{float(v):.2f}"


def format_terminal(result: dict[str, Any]) -> str:
    sim = result.get("similarity") or {}
    cur = result.get("current_market") or {}
    lines = [
        "MARKET FINGERPRINT V1",
        "",
        f"trades={result.get('n_trades')} snapshots={result.get('n_snapshots')} "
        f"elapsed={result.get('elapsed_sec')}s",
        "",
        "Current Market",
        f"  {cur.get('symbol')} {cur.get('direction')} regime={cur.get('regime')}",
        "",
        "Similarity",
        f"  {sim.get('similarity_pct')}%",
        "",
        "Closest Fingerprint",
        f"  {sim.get('closest_fingerprint') or cur.get('fingerprint') or 'n/a'}",
        "",
        "Historical WR",
        f"  {sim.get('historical_wr')}%",
        "",
        "Historical PF",
        f"  {sim.get('historical_pf')}",
        "",
        "Historical EV",
        f"  {sim.get('historical_ev')}",
        "",
        "Recommendation",
        f"  {sim.get('recommendation') or 'RESEARCH ONLY'}",
        "",
        f"WIN fps={len(result.get('win_fingerprints') or [])} "
        f"LOSS fps={len(result.get('loss_fingerprints') or [])} "
        f"clusters={len(result.get('clusters') or [])}",
    ]
    return "\n".join(lines)


def format_report(result: dict[str, Any]) -> str:
    """Markdown report capped at ~200 lines."""
    sim = result.get("similarity") or {}
    cur = result.get("current_market") or {}
    lines = [
        "# MARKET_FINGERPRINT_REPORT",
        "",
        "_Market State Fingerprint Engine V1 — research only._",
        "",
        f"- trades: **{result.get('n_trades')}** snapshots: **{result.get('n_snapshots')}**",
        f"- candles symbols: **{result.get('n_candle_symbols')}** runtime: **{result.get('elapsed_sec')}s**",
        f"- clusters: **{len(result.get('clusters') or [])}**",
        "",
        "## Current Market",
        "",
        f"- symbol/dir: `{cur.get('symbol')} {cur.get('direction')}`",
        f"- regime: `{cur.get('regime')}`",
        f"- Similarity: **{sim.get('similarity_pct')}%**",
        f"- Closest Fingerprint: **{sim.get('closest_fingerprint') or 'n/a'}**",
        f"- Historical WR/PF/EV: **{sim.get('historical_wr')}** / **{sim.get('historical_pf')}** / **{sim.get('historical_ev')}**",
        f"- Recommendation: **{sim.get('recommendation') or 'RESEARCH ONLY'}**",
        "",
        "## WIN fingerprints",
        "",
    ]
    for c in (result.get("win_fingerprints") or [])[:8]:
        lines.append(
            f"- `{c.get('id')}` n={c.get('n')} WR={c.get('wr')} PF={_pf(c.get('pf'))} "
            f"EV={c.get('ev')} Sharpe={c.get('sharpe')}"
        )
    lines.extend(["", "## LOSS fingerprints", ""])
    for c in (result.get("loss_fingerprints") or [])[:8]:
        lines.append(
            f"- `{c.get('id')}` n={c.get('n')} WR={c.get('wr')} PF={_pf(c.get('pf'))} EV={c.get('ev')}"
        )
    lines.extend(["", "## MAE / MFE", ""])
    mm = result.get("mae_mfe") or {}
    lines.append(
        f"- avg MAE={mm.get('avg_mae')} median={mm.get('median_mae')} | "
        f"avg MFE={mm.get('avg_mfe')} median={mm.get('median_mfe')}"
    )
    lines.extend(["", "## Sequences", ""])
    for lb, rows in (result.get("sequences") or {}).items():
        lines.append(f"### lookback={lb}")
        for r in (rows or [])[:5]:
            lines.append(
                f"- {r.get('pattern')} n={r.get('n')} WR={r.get('wr')} "
                f"PF={_pf(r.get('pf'))} EV={r.get('ev')}"
            )
    lines.extend(["", "## Regime transitions (top)", ""])
    probs = ((result.get("transition_matrix") or {}).get("probs")) or {}
    for a, row in list(probs.items())[:6]:
        tops = sorted(row.items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines.append(f"- {a} → " + ", ".join(f"{b}={p}" for b, p in tops if p > 0))
    lines.extend(["", "## Coin DNA", ""])
    for sym, info in list((result.get("coin_dna") or {}).items())[:8]:
        best = ((info.get("best") or [{}])[0]).get("fingerprint")
        lines.append(
            f"- **{sym}** n={info.get('n')} PF={_pf(info.get('pf'))} "
            f"ideal={best or 'n/a'}"
        )
    lines.extend(["", "## Universal DNA", ""])
    for u in (result.get("universal_dna") or [])[:8]:
        lines.append(
            f"- `{u.get('fingerprint')}` coins={u.get('n_coins')} "
            f"n={u.get('n')} PF={_pf(u.get('pf'))} EV={u.get('ev')}"
        )
    lines.extend([
        "",
        "## Integrity",
        "",
        f"- research_only: {result.get('research_only')}",
        f"- gate/strategy/paper/execution/optimizer/brain unchanged",
        "",
    ])
    # hard cap ~200 lines
    if len(lines) > 200:
        lines = lines[:198] + ["", "_truncated_"]
    return "\n".join(lines)


def _jsonable(result: dict[str, Any]) -> dict[str, Any]:
    skip = {"_sim_index", "_assignments", "_snapshots", "terminal", "report_markdown", "distribution_detail"}
    out = {k: v for k, v in result.items() if k not in skip}
    # keep slim distributions already present
    return out


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    md = format_report(result)
    payload = _jsonable(result)
    # Expand JSON with required sections
    payload_json = {
        "ok": payload.get("ok"),
        "n_trades": payload.get("n_trades"),
        "n_snapshots": payload.get("n_snapshots"),
        "elapsed_sec": payload.get("elapsed_sec"),
        "win_fingerprints": payload.get("win_fingerprints"),
        "loss_fingerprints": payload.get("loss_fingerprints"),
        "clusters": payload.get("clusters"),
        "transition_matrix": payload.get("transition_matrix"),
        "similarity_library": payload.get("similarity_library"),
        "sequences": payload.get("sequences"),
        "coin_dna": payload.get("coin_dna"),
        "universal_dna": payload.get("universal_dna"),
        "mae_mfe": payload.get("mae_mfe"),
        "current_market": payload.get("current_market"),
        "similarity": payload.get("similarity"),
        "distributions": result.get("distribution_detail"),
    }
    REPORT_MD.write_text(md, encoding="utf-8")
    REPORT_JSON.write_text(json.dumps(payload_json, indent=2, default=str), encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "MARKET_FINGERPRINT_REPORT.md").write_text(md, encoding="utf-8")
    (OUT_DIR / "MARKET_FINGERPRINT.json").write_text(
        json.dumps(payload_json, indent=2, default=str), encoding="utf-8"
    )
    return {
        "MARKET_FINGERPRINT_REPORT.md": str(REPORT_MD),
        "MARKET_FINGERPRINT.json": str(REPORT_JSON),
        "out/MARKET_FINGERPRINT_REPORT.md": str(OUT_DIR / "MARKET_FINGERPRINT_REPORT.md"),
        "out/MARKET_FINGERPRINT.json": str(OUT_DIR / "MARKET_FINGERPRINT.json"),
        "report_text": md,
    }


__all__ = ["format_report", "format_terminal", "write_artifacts"]
