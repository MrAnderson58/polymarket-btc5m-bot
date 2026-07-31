"""Part 3 — End-to-end data-flow audit with bottleneck detection."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPORT_DIR = BASE_DIR / "reports" / "research"
PIPELINE_MD = REPORT_DIR / "PIPELINE_FLOW_AUDIT.md"
PIPELINE_JSON = REPORT_DIR / "pipeline_flow_audit.json"

CRITICAL_DROP = 0.90  # >90% loss → CRITICAL BOTTLENECK


def _count(conn: Any, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] if row and row[0] is not None else 0)
    except Exception:
        return 0


def _top_reasons(conn: Any, sql: str, params: tuple = (), limit: int = 8) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows[:limit]:
            out.append({"reason": r[0], "n": int(r[1])})
        return out
    except Exception:
        return []


def run_pipeline_flow_audit(
    conn: Any,
    *,
    write_reports: bool = True,
    report_dir: Path | None = None,
    hours: int = 24,
) -> dict[str, Any]:
    """Walk Market → … → ML and measure drop-offs."""
    t0 = time.time()
    since = int(time.time()) - int(hours) * 3600
    now = int(time.time())

    snapshots_all = _count(conn, "SELECT COUNT(*) FROM market_snapshots_g3")
    snapshots_win = _count(
        conn, "SELECT COUNT(*) FROM market_snapshots_g3 WHERE snapshot_ts >= ?", (since,),
    )
    candles = _count(conn, "SELECT COUNT(*) FROM market_events_historical_candles")
    candidates_all = _count(conn, "SELECT COUNT(*) FROM market_candidate_g31")
    candidates_win = _count(
        conn, "SELECT COUNT(*) FROM market_candidate_g31 WHERE created_at >= ?", (since,),
    )
    candidates_rej = _count(
        conn,
        "SELECT COUNT(*) FROM market_candidate_g31 WHERE created_at >= ? AND candidate_state = 'rejected'",
        (since,),
    )
    s40_all = _count(conn, "SELECT COUNT(*) FROM market_events_signal_learning_s40_signals")
    s40_win = _count(
        conn,
        "SELECT COUNT(*) FROM market_events_signal_learning_s40_signals WHERE created_at >= ?",
        (since,),
    )
    s55_all = _count(conn, "SELECT COUNT(*) FROM market_events_trade_features_s55")
    s55_win = _count(
        conn,
        "SELECT COUNT(*) FROM market_events_trade_features_s55 WHERE created_at >= ?",
        (since,),
    )
    s55_with_paper = _count(
        conn,
        "SELECT COUNT(*) FROM market_events_trade_features_s55 WHERE paper_trade_id IS NOT NULL",
    )
    gate_allowed = _count(
        conn,
        """
        SELECT COUNT(*) FROM market_events_trade_features_s55
        WHERE gate_decision IN ('ALLOWED','REGIME_EXPLORE','EXPLORE')
        """,
    )
    s42_all = _count(conn, "SELECT COUNT(*) FROM market_events_paper_trades_s42")
    s42_open = _count(conn, "SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE status='OPEN'")
    s42_closed = _count(
        conn, "SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE status='CLOSED'",
    )
    s42_win = _count(
        conn,
        "SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE created_at >= ?",
        (since,),
    )
    s42_closed_win = _count(
        conn,
        "SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE closed_at IS NOT NULL AND closed_at >= ?",
        (since,),
    )

    # Feature store / ML artifacts
    fs_dir = BASE_DIR / "research" / "ml" / "feature_store" / "v1"
    ds_csv = BASE_DIR / "research" / "ml" / "datasets" / "v1" / "training_dataset.csv"
    ml_samples = 0
    if ds_csv.exists():
        try:
            ml_samples = max(0, sum(1 for _ in ds_csv.open()) - 1)
        except Exception:
            ml_samples = 0
    fs_files = len(list(fs_dir.glob("*"))) if fs_dir.exists() else 0

    reject_g31 = _top_reasons(
        conn,
        """
        SELECT rejection_reason, COUNT(*) n FROM market_candidate_g31
        WHERE created_at >= ? GROUP BY 1 ORDER BY n DESC
        """,
        (since,),
    )
    reject_gate = _top_reasons(
        conn,
        """
        SELECT gate_decision, COUNT(*) n FROM market_events_trade_features_s55
        WHERE created_at >= ? GROUP BY 1 ORDER BY n DESC
        """,
        (since,),
    )

    stages = [
        {
            "name": "Market / Snapshots",
            "input_rows": None,
            "output_rows": snapshots_all,
            "window_rows": snapshots_win,
            "drop_pct": None,
            "reject_reason": None,
            "notes": f"candles={candles}",
        },
        {
            "name": "Collector → Candidates (G31)",
            "input_rows": snapshots_win if snapshots_win else snapshots_all,
            "output_rows": candidates_all,
            "window_rows": candidates_win,
            "drop_pct": None,
            "reject_reason": reject_g31,
            "notes": f"rejected_in_window={candidates_rej}",
        },
        {
            "name": "Candidates → S40 Signals",
            "input_rows": candidates_win if candidates_win else candidates_all,
            "output_rows": s40_all,
            "window_rows": s40_win,
            "drop_pct": _drop(candidates_win or candidates_all, s40_win or 0),
            "reject_reason": None,
            "notes": None,
        },
        {
            "name": "Signals → Features (S55)",
            "input_rows": s40_win if s40_win else s40_all,
            "output_rows": s55_all,
            "window_rows": s55_win,
            "drop_pct": _drop(s40_win or s40_all, s55_win or 0),
            "reject_reason": reject_gate,
            "notes": "S55 includes blocked attempts (paper_trade_id NULL)",
        },
        {
            "name": "Gate (kept vs logged)",
            "input_rows": s55_all,
            "output_rows": gate_allowed,
            "window_rows": None,
            "drop_pct": _drop(s55_all, gate_allowed),
            "reject_reason": reject_gate,
            "notes": "ALLOWED/REGIME_EXPLORE/EXPLORE counted as pass",
        },
        {
            "name": "Gate → Paper (S42)",
            "input_rows": s55_with_paper if s55_with_paper else gate_allowed,
            "output_rows": s42_all,
            "window_rows": s42_win,
            "drop_pct": _drop(max(s55_all, 1), s42_all),
            "reject_reason": reject_gate,
            "notes": f"open={s42_open} linked_s55={s55_with_paper}",
        },
        {
            "name": "Paper → Closed",
            "input_rows": s42_all,
            "output_rows": s42_closed,
            "window_rows": s42_closed_win,
            "drop_pct": _drop(s42_all, s42_closed) if s42_all else 0.0,
            "reject_reason": None,
            "notes": f"still_open={s42_open}",
        },
        {
            "name": "Closed → Research / Feature Store",
            "input_rows": s42_closed,
            "output_rows": fs_files,
            "window_rows": None,
            "drop_pct": None,
            "reject_reason": None,
            "notes": f"feature_store_v1 files={fs_files}",
        },
        {
            "name": "Feature Store → ML dataset",
            "input_rows": s42_closed,
            "output_rows": ml_samples,
            "window_rows": None,
            "drop_pct": _drop(s42_closed, ml_samples) if s42_closed else None,
            "reject_reason": None,
            "notes": str(ds_csv) if ds_csv.exists() else "training_dataset.csv missing",
        },
    ]

    # Mark critical bottlenecks on consecutive flow using window or all-time
    bottlenecks: list[dict[str, Any]] = []
    flow_pairs = [
        ("Candidates(window)", candidates_win, "S55(window)", s55_win),
        ("S55(all)", s55_all, "Gate-pass", gate_allowed),
        ("S55(all)", s55_all, "S42 paper", s42_all),
        ("S40(window)", s40_win, "S42(window opens)", s42_win),
    ]
    for a_name, a_n, b_name, b_n in flow_pairs:
        if a_n and a_n > 0:
            drop = 1.0 - (float(b_n) / float(a_n))
            if drop >= CRITICAL_DROP:
                bottlenecks.append({
                    "from": a_name,
                    "to": b_name,
                    "input": a_n,
                    "output": b_n,
                    "drop_pct": round(100.0 * drop, 2),
                    "label": "CRITICAL BOTTLENECK",
                })

    # Annotate stages
    for st in stages:
        d = st.get("drop_pct")
        if d is not None and d >= CRITICAL_DROP * 100:
            st["critical"] = True
            st["label"] = "CRITICAL BOTTLENECK"
        else:
            st["critical"] = False
            st["label"] = None

    elapsed_ms = round((time.time() - t0) * 1000, 1)
    result = {
        "ok": True,
        "hours": hours,
        "generated_at": now,
        "processing_time_ms": elapsed_ms,
        "stages": stages,
        "bottlenecks": bottlenecks,
        "counts": {
            "snapshots_all": snapshots_all,
            "snapshots_window": snapshots_win,
            "candidates_all": candidates_all,
            "candidates_window": candidates_win,
            "s40_all": s40_all,
            "s40_window": s40_win,
            "s55_all": s55_all,
            "s55_window": s55_win,
            "gate_pass": gate_allowed,
            "s42_all": s42_all,
            "s42_closed": s42_closed,
            "s42_open": s42_open,
            "ml_samples": ml_samples,
        },
        "read_only": True,
    }
    md = format_pipeline_flow_md(result)
    result["report_markdown"] = md
    if write_reports:
        out = Path(os.environ.get("PIPELINE_AUDIT_DIR", str(report_dir or REPORT_DIR)))
        out.mkdir(parents=True, exist_ok=True)
        (out / "PIPELINE_FLOW_AUDIT.md").write_text(md, encoding="utf-8")
        (out / "pipeline_flow_audit.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8",
        )
        result["report_paths"] = {
            "md": str(out / "PIPELINE_FLOW_AUDIT.md"),
            "json": str(out / "pipeline_flow_audit.json"),
        }
    return result


def _drop(inp: int, out: int) -> float | None:
    if not inp:
        return None
    return round(100.0 * max(0.0, 1.0 - float(out) / float(inp)), 2)


def format_pipeline_flow_md(result: dict[str, Any]) -> str:
    lines = [
        "# Pipeline Flow Audit — Signal Mathematics Recovery",
        "",
        f"_Window={result.get('hours')}h | processing_time_ms={result.get('processing_time_ms')}_",
        "",
        "```",
        "Market → Collector → Candidate → Features → Gate → S55 → S42 → Close → Research → Feature Store → ML",
        "```",
        "",
        "## Flow",
        "",
    ]
    for st in result.get("stages") or []:
        crit = " **CRITICAL BOTTLENECK**" if st.get("critical") else ""
        lines.append(
            f"### {st['name']}{crit}\n"
            f"- input_rows: `{st.get('input_rows')}`\n"
            f"- output_rows: `{st.get('output_rows')}`\n"
            f"- window_rows: `{st.get('window_rows')}`\n"
            f"- drop_%: `{st.get('drop_pct')}`\n"
            f"- notes: {st.get('notes')}\n"
        )
        if st.get("reject_reason"):
            lines.append("- reject reasons:")
            for r in st["reject_reason"][:8]:
                lines.append(f"  - `{r.get('reason')}`: {r.get('n')}")
            lines.append("")
    lines.extend(["## Critical bottlenecks", ""])
    bots = result.get("bottlenecks") or []
    if not bots:
        lines.append("_None above 90% drop threshold._")
    for b in bots:
        lines.append(
            f"- **CRITICAL BOTTLENECK** `{b['from']}` → `{b['to']}`: "
            f"{b['input']} → {b['output']} (drop {b['drop_pct']}%)"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = ["run_pipeline_flow_audit", "format_pipeline_flow_md"]
