"""Shock event lifecycle audit report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.db_helpers import row_get, scalar
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.event_types import EXIT_IDS, REVERSAL_IDS
from bot.research.market_events.lifecycle_decisions import load_lifecycle_decisions


def _ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _reversal_status(decisions: list[dict], variant: str) -> tuple[str, str]:
    stage = f"REVERSAL_{variant}"
    matches = [d for d in decisions if d.get("stage") == stage]
    if not matches:
        return "not_logged", "no persisted decision (pre-E.3 event or not evaluated)"
    latest = matches[-1]
    return str(latest.get("status", "?")), str(latest.get("reason") or "")


def _run_stats(conn: Any, event_id: int) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM paper_strategy_runs WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    entered = [r for r in rows if r["entry_ts"]]
    closed = [r for r in rows if r["exit_ts"]]
    open_runs = [r for r in rows if r["entry_ts"] and not r["exit_ts"]]
    waiting = [r for r in rows if not r["entry_ts"]]
    exits = sorted({r["exit_variant"] for r in rows})
    mfe_vals = [float(r["mfe"]) for r in closed if r["mfe"] is not None]
    mae_vals = [float(r["mae"]) for r in closed if r["mae"] is not None]
    ret_vals = [float(r["net_return"]) for r in closed if r["net_return"] is not None]
    return {
        "total_runs": len(rows),
        "waiting_entry": len(waiting),
        "entered": len(entered),
        "closed": len(closed),
        "open": len(open_runs),
        "expired_no_entry": len(waiting),
        "exits": exits,
        "mfe": max(mfe_vals) if mfe_vals else None,
        "mae": max(mae_vals) if mae_vals else None,
        "realized_return": sum(ret_vals) / len(ret_vals) if ret_vals else None,
    }


def shock_lifecycle_audit(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    events = conn.execute(
        "SELECT * FROM market_events WHERE event_ts >= ? ORDER BY id",
        (since,),
    ).fetchall()
    lines = [
        "SHOCK LIFECYCLE AUDIT",
        f"window_days: {days}",
        f"events: {len(events)}",
        "",
        "NOTE: E.1 evaluates reversals once at shock detection time.",
        "If no reversal confirms on that poll, no paper_strategy_runs are created.",
        "",
    ]
    for e in events:
        eid = int(e["id"])
        triggers = json.loads(e["detector_triggers_json"] or "[]")
        decisions = load_lifecycle_decisions(conn, eid)
        stats = _run_stats(conn, eid)
        lines.extend([
            f"--- event_id={eid} ---",
            f"  timestamp: {_ts(e['event_ts'])}",
            f"  symbol: {e['symbol']} direction: {e['direction']}",
            f"  detectors: {', '.join(triggers) if triggers else '(none)'}",
            f"  trigger_window_sec: {e['trigger_window_seconds']}",
            f"  return_pct: {e['return_pct']}",
            f"  volume_zscore: {e['volume_zscore']}",
            f"  classification: {e['classification']}",
            f"  reversal_candidates_evaluated: {len(REVERSAL_IDS)}",
        ])
        for rv in REVERSAL_IDS:
            status, reason = _reversal_status(decisions, rv)
            lines.append(f"  R{rv[-1]} status={status} reason={reason}")
        lines.extend([
            f"  paper_runs_created: {stats['total_runs']}",
            f"  waiting_for_entry: {stats['waiting_entry']}",
            f"  entered: {stats['entered']}",
            f"  closed: {stats['closed']}",
            f"  open: {stats['open']}",
            f"  expired_no_entry: {stats['expired_no_entry']}",
            f"  exits_attached: {', '.join(stats['exits']) or 'none'}",
            f"  MFE: {stats['mfe']}",
            f"  MAE: {stats['mae']}",
            f"  realized_return: {stats['realized_return']}",
            "",
        ])
        if eid == 1 or (e["symbol"] == "SUI" and stats["closed"] == 0):
            lines.extend(_explain_sui_r1_zero(e, decisions, stats))
    return "\n".join(lines)


def _explain_sui_r1_zero(e: Any, decisions: list[dict], stats: dict) -> list[str]:
    lines = [
        "  === EXPLANATION (SUI / zero closed runs) ===",
    ]
    if stats["total_runs"] == 0:
        lines.append("  paper_strategy_runs=0 because no reversal confirmed on detection poll.")
        r1_status, r1_reason = _reversal_status(decisions, "R1")
        if r1_status == "not_logged":
            lines.append(
                "  R1: not persisted (pre-E.3). At detection, R1 requires price reclaim "
                f"of {e['return_pct']:.2f}% shock move; single-shot evaluation only.",
            )
        else:
            lines.append(f"  R1: status={r1_status} reason={r1_reason}")
        lines.append(
            "  Architecture: E.1 does not re-poll pending reversals after initial shock cycle.",
        )
    return lines
