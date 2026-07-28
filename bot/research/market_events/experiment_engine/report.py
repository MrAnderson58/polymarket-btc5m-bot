"""experiment-show CLI and reports/research/experiments.md."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.market_events.experiment_engine.engine import (
    load_experiment_snapshot,
    run_all_experiments,
)
from bot.research.market_events.experiment_engine.schema import ensure_experiment_engine_schema


def _line(e: dict[str, Any]) -> str:
    return (
        f"#{e.get('id')} [{e.get('status')}] {e.get('experiment_type')} "
        f"hyp=#{e.get('hypothesis_id')} {e.get('hypothesis_title') or e.get('hypothesis_key') or ''}  "
        f"ΔEV={e.get('delta_ev')} d={e.get('effect_size')} p={e.get('p_value')} "
        f"n={e.get('dataset_size')}"
    )


def format_experiment_show(conn: Any) -> str:
    ensure_experiment_engine_schema(conn)
    snap = load_experiment_snapshot(conn)
    lines = [
        "EXPERIMENT ENGINE V1",
        "",
        "TOP VALIDATED",
    ]
    for e in (snap["validated"] or [])[:10]:
        lines.append(f"  {_line(e)}")
    if not snap["validated"]:
        lines.append("  (none)")

    lines.extend(["", "TOP REJECTED"])
    for e in (snap["rejected"] or [])[:10]:
        lines.append(f"  {_line(e)}")
    if not snap["rejected"]:
        lines.append("  (none)")

    lines.extend(["", "RUNNING"])
    for e in (snap["running"] or [])[:10]:
        lines.append(f"  {_line(e)}")
    if not snap["running"]:
        lines.append("  (none)")

    lines.extend(["", "LAST RUN"])
    last = snap.get("last_run")
    if last:
        lines.append(
            f"  run#{last.get('id')} exp=#{last.get('experiment_id')} "
            f"type={last.get('experiment_type')} success={last.get('success')} "
            f"ms={last.get('duration_ms')} hash={last.get('dataset_hash')}"
        )
    else:
        lines.append("  (none)")

    lines.extend(["", "BEST EFFECT SIZE"])
    for e in (snap["best_effect"] or [])[:10]:
        lines.append(f"  {_line(e)}")
    if not snap["best_effect"]:
        lines.append("  (none)")

    lines.extend(["", "MOST RELIABLE"])
    for e in (snap["most_reliable"] or [])[:10]:
        lines.append(f"  {_line(e)}")
    if not snap["most_reliable"]:
        lines.append("  (none)")

    return "\n".join(lines)


def write_experiments_report(conn: Any, root: Path | None = None) -> Path:
    snap = load_experiment_snapshot(conn)
    out_dir = root or Path("reports/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "experiments.md"
    lines = [
        "# Experiment Engine V1",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "_Historical hypothesis checks only. Not trade signals. No Gate / strategy / Trading Core changes._",
        "",
        "## Validated Experiments",
        "",
    ]
    if not snap["validated"]:
        lines.append("_None_")
    for e in snap["validated"]:
        lines.append(f"### {_line(e)}")
        lines.append("")
        lines.append(
            f"- EV {e.get('ev_before')} → {e.get('ev_after')}  "
            f"PF {e.get('pf_before')} → {e.get('pf_after')}  "
            f"WR {e.get('wr_before')} → {e.get('wr_after')}"
        )
        lines.append(
            f"- effect_size={e.get('effect_size')}  p={e.get('p_value')}  "
            f"CI={e.get('confidence_interval')}  MFE={e.get('mfe_after')} MAE={e.get('mae_after')}"
        )
        if e.get("notes"):
            lines.append(f"- notes: {e.get('notes')}")
        lines.append("")

    lines.extend(["## Rejected Experiments", ""])
    if not snap["rejected"]:
        lines.append("_None_")
    for e in snap["rejected"][:25]:
        lines.append(f"- {_line(e)}")
        if e.get("notes"):
            lines.append(f"  - {e.get('notes')}")

    lines.extend(["", "## Strongest Evidence", ""])
    if not snap["best_effect"]:
        lines.append("_None_")
    for e in snap["best_effect"][:10]:
        lines.append(
            f"- {_line(e)}  (|d|={abs(float(e.get('effect_size') or 0)):.4f})"
        )

    lines.extend(["", "## Weak Evidence", ""])
    weak = [e for e in snap["experiments"] if e.get("status") in ("WEAK", "FAILED")]
    if not weak:
        lines.append("_None_")
    for e in weak[:20]:
        lines.append(f"- {_line(e)}")

    lines.extend(["", "## Recent Runs", ""])
    if not snap["runs"]:
        lines.append("_None_")
    for r in snap["runs"][:30]:
        when = r.get("run_time")
        ts = (
            datetime.fromtimestamp(int(when), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            if when
            else "?"
        )
        lines.append(
            f"- {ts} run#{r.get('id')} exp=#{r.get('experiment_id')} "
            f"{r.get('experiment_type')} success={r.get('success')} "
            f"{r.get('duration_ms')}ms hash={r.get('dataset_hash')}"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def format_experiment_run_summary(summary: dict[str, Any]) -> str:
    lines = [
        "EXPERIMENT RUN",
        f"  hypotheses={summary.get('n_hypotheses')} trades={summary.get('n_trades')} "
        f"ran={summary.get('ran')}",
        f"  validated={summary.get('validated')} rejected={summary.get('rejected')} "
        f"weak={summary.get('weak')} failed={summary.get('failed')}",
        f"  knowledge_updates={summary.get('knowledge_updates')}",
    ]
    if summary.get("error"):
        lines.append(f"  error={summary['error']}")
    lines.append("")
    lines.append("Results:")
    for e in (summary.get("experiments") or [])[:20]:
        lines.append(
            f"  #{e.get('id')} [{e.get('status')}] {e.get('experiment_type')} "
            f"ΔEV={e.get('delta_ev')} d={e.get('effect_size')} "
            f"→ hyp {e.get('hypothesis_status')}: {e.get('hypothesis_title')}"
        )
    return "\n".join(lines)


def run_experiment_cli(conn: Any, *, write_reports: bool = True, patterns_root: Path | None = None) -> str:
    summary = run_all_experiments(conn, patterns_root=patterns_root)
    text = format_experiment_run_summary(summary)
    if write_reports:
        path = write_experiments_report(conn)
        text += f"\n\nWrote {path}"
    text += "\n\n" + format_experiment_show(conn)
    return text
