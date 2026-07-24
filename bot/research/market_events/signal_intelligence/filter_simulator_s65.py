"""S65 — Strategy Filter Simulator (research-only).

Counterfactual: what happens to total PnL if we stop taking a PnL-killer segment?
Builds on S64 killers. Statistics only — no strategy auto-apply.
"""

from __future__ import annotations

import json
import logging
import time
from itertools import combinations
from pathlib import Path
from typing import Any

from bot.research.market_events.signal_intelligence.drift_analyzer_s622 import (
    _enrich_from_snapshot_json,
)
from bot.research.market_events.signal_intelligence.feature_lab_s59 import load_lab_trades
from bot.research.market_events.signal_intelligence.pnl_killers_s64 import (
    DIM_LABEL,
    TOP_N,
    _segment_metrics,
    build_dimension_tables,
    dimension_value,
    pnl_report_dir,
    rank_generators,
)
from bot.research.market_events.signal_intelligence.trading_intelligence_report_s621 import (
    _enrich_decisions,
)

logger = logging.getLogger(__name__)

TOP_FILTERS = 20
# 2D combos among these dimensions (e.g. "Remove LONG in Range")
COMBO_DIMS = ("direction", "regime", "hour", "strategy", "coin")


def _fmt(v: Any, *, digits: int = 4) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 100:
        return f"{f:.2f}"
    return f"{f:.{digits}g}"


def _fmt_signed(v: Any) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    sign = "+" if f > 0 else ""
    return f"{sign}{_fmt(f)}"


def _pf_out(m: dict[str, Any]) -> float | None:
    if m.get("pf_inf"):
        return None
    return m.get("profit_factor")


def remove_label_single(dimension: str, key: str) -> str:
    pretty = DIM_LABEL.get(dimension, dimension)
    if dimension == "strategy":
        return f"Remove {key}"
    if dimension == "hour":
        return f"Remove Hour {key.replace('H', '')}" if str(key).startswith("H") else f"Remove {pretty}={key}"
    return f"Remove {pretty} = {key}"


def remove_label_combo(parts: list[tuple[str, str]]) -> str:
    """Human label for multi-dim remove (e.g. LONG in Range)."""
    by = {d: k for d, k in parts}
    if "direction" in by and "regime" in by and len(parts) == 2:
        return f"Remove {by['direction']} in {by['regime']}"
    if "direction" in by and "hour" in by and len(parts) == 2:
        return f"Remove {by['direction']} at {by['hour']}"
    if "strategy" in by and len(parts) == 1:
        return f"Remove {by['strategy']}"
    bits = [f"{DIM_LABEL.get(d, d)}={k}" for d, k in parts]
    return "Remove " + " + ".join(bits)


def simulate_remove(
    rows: list[dict[str, Any]],
    *,
    predicates: list[tuple[str, str]],
    remove_label: str,
    source: str,
) -> dict[str, Any] | None:
    """Drop trades matching ALL (dimension, key) predicates; compare vs baseline."""
    if not rows or not predicates:
        return None

    def _hit(r: dict[str, Any]) -> bool:
        return all(dimension_value(r, dim) == key for dim, key in predicates)

    kept = [r for r in rows if not _hit(r)]
    removed_n = len(rows) - len(kept)
    if removed_n <= 0:
        return None
    if not kept:
        # Removing everything is not a useful filter
        return None

    old_m = _segment_metrics(rows)
    new_m = _segment_metrics(kept)
    old_pnl = float(old_m.get("net_pnl") or 0.0)
    new_pnl = float(new_m.get("net_pnl") or 0.0)
    delta = round(new_pnl - old_pnl, 4)

    return {
        "remove_label": remove_label,
        "source": source,
        "predicates": [{"dimension": d, "key": k} for d, k in predicates],
        "trades_removed": removed_n,
        "remaining_trades": len(kept),
        "old_pnl": old_pnl,
        "new_pnl": new_pnl,
        "delta_pnl": delta,
        "improvement": delta,
        "old_pf": _pf_out(old_m),
        "new_pf": _pf_out(new_m),
        "old_pf_inf": bool(old_m.get("pf_inf")),
        "new_pf_inf": bool(new_m.get("pf_inf")),
        "old_wr": old_m.get("win_rate"),
        "new_wr": new_m.get("win_rate"),
        "old_max_dd": old_m.get("max_dd"),
        "new_max_dd": new_m.get("max_dd"),
        "killer_net_pnl": None,
    }


def _combo_candidates(killers: list[dict[str, Any]]) -> list[list[tuple[str, str]]]:
    """Build 2D remove specs from top killers on complementary dimensions."""
    by_dim: dict[str, list[dict[str, Any]]] = {}
    for k in killers:
        dim = str(k.get("dimension") or "")
        if dim not in COMBO_DIMS:
            continue
        by_dim.setdefault(dim, []).append(k)

    out: list[list[tuple[str, str]]] = []
    dims_present = [d for d in COMBO_DIMS if by_dim.get(d)]
    for d1, d2 in combinations(dims_present, 2):
        # top 3 killers per dimension to keep runtime small
        for a in by_dim[d1][:3]:
            for b in by_dim[d2][:3]:
                out.append([(d1, str(a["key"])), (d2, str(b["key"]))])
    return out


def run_filter_simulations(
    rows: list[dict[str, Any]],
    *,
    killers: list[dict[str, Any]],
    top_n: int = TOP_FILTERS,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for killer in killers:
        dim = str(killer.get("dimension") or "")
        key = str(killer.get("key") or "")
        if not dim or not key:
            continue
        label = remove_label_single(dim, key)
        sim = simulate_remove(
            rows,
            predicates=[(dim, key)],
            remove_label=label,
            source="s64_killer",
        )
        if not sim:
            continue
        sim["killer_net_pnl"] = killer.get("net_pnl")
        sim["killer_label"] = killer.get("label")
        sig = json.dumps(sim["predicates"], sort_keys=True)
        if sig in seen:
            continue
        seen.add(sig)
        results.append(sim)

    for preds in _combo_candidates(killers):
        label = remove_label_combo(preds)
        sim = simulate_remove(
            rows,
            predicates=preds,
            remove_label=label,
            source="combo",
        )
        if not sim:
            continue
        sig = json.dumps(sim["predicates"], sort_keys=True)
        if sig in seen:
            continue
        seen.add(sig)
        results.append(sim)

    results.sort(
        key=lambda r: (-float(r.get("delta_pnl") or 0.0), -int(r.get("trades_removed") or 0)),
    )
    return results[:top_n] if top_n else results


def format_filter_simulator_markdown(report: dict[str, Any]) -> str:
    baseline = report.get("baseline") or {}
    lines = [
        "# Strategy Filter Simulator (S65)",
        "",
        f"_trades={report.get('n_trades')} baseline_pnl={_fmt(baseline.get('net_pnl'))} "
        f"elapsed={report.get('elapsed_sec')}s_",
        "",
        "Counterfactual only. No strategy auto-apply. Built from S64 PnL killers.",
        "",
        "## Baseline",
        "",
        f"- Trades: **{baseline.get('trades')}**",
        f"- Net PnL: **{_fmt(baseline.get('net_pnl'))}**",
        f"- PF: **{'∞' if baseline.get('pf_inf') else _fmt(baseline.get('profit_factor'))}**",
        f"- WR: **{_fmt(baseline.get('win_rate'))}%**",
        f"- Max DD: **{_fmt(baseline.get('max_dd'))}**",
        "",
        "## TOP FILTERS",
        "",
    ]

    top = report.get("top_filters") or []
    if not top:
        lines.append("_No improving filters found._\n")
    for i, f in enumerate(top, 1):
        lines.extend([
            f"### {i}. {f.get('remove_label')}",
            "",
            f"**Improvement: {_fmt_signed(f.get('delta_pnl'))}**",
            "",
            f"- Trades removed: **{f.get('trades_removed')}**",
            f"- Remaining trades: **{f.get('remaining_trades')}**",
            f"- Old Net PnL: **{_fmt(f.get('old_pnl'))}**",
            f"- New Net PnL: **{_fmt(f.get('new_pnl'))}**",
            f"- Delta PnL: **{_fmt_signed(f.get('delta_pnl'))}**",
            f"- Old PF: **{'∞' if f.get('old_pf_inf') else _fmt(f.get('old_pf'))}** → "
            f"New PF: **{'∞' if f.get('new_pf_inf') else _fmt(f.get('new_pf'))}**",
            f"- Old WR: **{_fmt(f.get('old_wr'))}%** → New WR: **{_fmt(f.get('new_wr'))}%**",
            f"- Old MaxDD: **{_fmt(f.get('old_max_dd'))}** → New MaxDD: **{_fmt(f.get('new_max_dd'))}**",
            "",
        ])

    lines.extend([
        "## All simulated filters",
        "",
        "| # | Remove | Removed | Remaining | Old PnL | New PnL | Δ PnL | Old PF | New PF | Old WR | New WR | Old DD | New DD |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    sims = report.get("simulations") or []
    if not sims:
        lines.append("| — | _none_ | | | | | | | | | | | |")
    for i, f in enumerate(sims, 1):
        lines.append(
            f"| {i} | {f.get('remove_label')} | {f.get('trades_removed')} | "
            f"{f.get('remaining_trades')} | {_fmt(f.get('old_pnl'))} | {_fmt(f.get('new_pnl'))} | "
            f"{_fmt_signed(f.get('delta_pnl'))} | "
            f"{'∞' if f.get('old_pf_inf') else _fmt(f.get('old_pf'))} | "
            f"{'∞' if f.get('new_pf_inf') else _fmt(f.get('new_pf'))} | "
            f"{_fmt(f.get('old_wr'))} | {_fmt(f.get('new_wr'))} | "
            f"{_fmt(f.get('old_max_dd'))} | {_fmt(f.get('new_max_dd'))} |"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_filter_simulator_summary(report: dict[str, Any]) -> str:
    baseline = report.get("baseline") or {}
    lines = [
        "S65 Strategy Filter Simulator",
        f"  trades={report.get('n_trades')} baseline_pnl={baseline.get('net_pnl')} "
        f"elapsed={report.get('elapsed_sec')}s "
        f"filters={len(report.get('simulations') or [])}",
    ]
    for i, f in enumerate((report.get("top_filters") or [])[:5], 1):
        lines.append(
            f"  {i}. {f.get('remove_label')}  Δ={_fmt_signed(f.get('delta_pnl'))} "
            f"removed={f.get('trades_removed')}"
        )
    for k, p in (report.get("export_paths") or {}).items():
        lines.append(f"  {k}: {p}")
    return "\n".join(lines)


def run_filter_simulator(
    conn: Any,
    *,
    report_dir: Path | None = None,
    top_killers: int = TOP_N,
    top_filters: int = TOP_FILTERS,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    rows = load_lab_trades(conn)
    _enrich_decisions(conn, rows)
    _enrich_from_snapshot_json(rows)
    closed = [r for r in rows if r.get("pnl_usd") is not None]

    by_dimension = build_dimension_tables(closed)
    _profit, killers = rank_generators(by_dimension, top_n=top_killers)
    baseline = _segment_metrics(closed)
    simulations = run_filter_simulations(closed, killers=killers, top_n=0)
    # keep full list sorted; top_filters is the ranked cut
    top = [s for s in simulations if float(s.get("delta_pnl") or 0.0) > 0][:top_filters]
    if not top:
        top = simulations[:top_filters]

    out_dir = Path(report_dir) if report_dir else pnl_report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "filter_simulator.md"
    json_path = out_dir / "filter_simulator.json"

    report: dict[str, Any] = {
        "ok": True,
        "stage": "S65",
        "n_trades": len(closed),
        "baseline": baseline,
        "killers_used": [
            {
                "dimension": k.get("dimension"),
                "key": k.get("key"),
                "label": k.get("label"),
                "net_pnl": k.get("net_pnl"),
                "trades": k.get("trades"),
            }
            for k in killers
        ],
        "simulations": simulations,
        "top_filters": top,
        "elapsed_sec": round(time.perf_counter() - t0, 3),
        "export_paths": {
            "markdown": str(md_path),
            "json": str(json_path),
        },
    }
    md_path.write_text(format_filter_simulator_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


__all__ = [
    "format_filter_simulator_markdown",
    "format_filter_simulator_summary",
    "run_filter_simulator",
    "simulate_remove",
]
