"""Evolution shadow experiment — counterfactual entry tracking (observe-only)."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from bot.config import effective_entry_threshold
from bot.evolution.constants import SHADOW_PF_IMPROVEMENT_MIN, SHADOW_TARGET_SAMPLE
from bot.evolution.shadow_db import (
    complete_shadow_experiment,
    create_shadow_experiment,
    fetch_unevaluated_trades,
    get_latest_shadow,
    get_running_shadow,
    insert_shadow_evaluation,
    load_shadow_evaluations,
)
from bot.evolution.metrics import lane_metrics, trade_pnl_percent

logger = logging.getLogger(__name__)

PARAMETER_ALIASES = {
    "entry": "entry_threshold",
    "entry_threshold": "entry_threshold",
    "stop_loss": "stop_loss",
    "trailing_activation": "trailing_activation",
    "trailing_distance": "trailing_distance",
}


def normalize_parameter(parameter: str) -> str:
    return PARAMETER_ALIASES.get(parameter, parameter)


def shadow_would_enter(entry_price: float, threshold_base: float) -> bool:
    """For entry_threshold parameter: would have entered at lower threshold."""
    threshold = effective_entry_threshold(float(threshold_base))
    return float(entry_price) <= threshold


def evaluate_shadow_decision_for_trade(
    trade: Any,
    *,
    parameter: str,
    shadow_value: float,
    current_value: float,
) -> str:
    """
    Multi-parameter counterfactual:
    - entry_threshold: would the trade have entered at the shadow threshold?
    - stop_loss: always WOULD_ENTER (trade entered; stop outcome differs)
    - trailing_*: always WOULD_ENTER (trade entered; trailing outcome differs)

    Returns WOULD_ENTER or WOULD_SKIP.
    """
    param = normalize_parameter(parameter)
    if param == "entry_threshold":
        entry_price = float(trade["entry_price"])
        if shadow_would_enter(entry_price, shadow_value):
            return "WOULD_ENTER"
        return "WOULD_SKIP"
    # For stop/trailing: the trade always enters, only exit behavior differs
    return "WOULD_ENTER"


def evaluate_shadow_decision(
    entry_price: float,
    *,
    shadow_value: float,
) -> tuple[str, float, float]:
    """Legacy entry-only interface for backwards compat."""
    if shadow_would_enter(entry_price, shadow_value):
        return "WOULD_ENTER", 1.0, 1.0
    return "WOULD_SKIP", 1.0, 0.0


def _candidate_values(candidate: dict[str, Any]) -> tuple[str, float, float]:
    param = normalize_parameter(str(candidate.get("parameter", "entry_threshold")))
    current = float(candidate["from_value"])
    shadow = float(candidate["to_value"])
    return param, current, shadow


def maybe_create_shadow_experiment(
    conn: sqlite3.Connection,
    *,
    candidate: dict[str, Any] | None,
    ready_for_shadow: bool,
) -> dict[str, Any] | None:
    if not ready_for_shadow or not candidate:
        return get_running_shadow(conn)
    if get_running_shadow(conn) is not None:
        return get_running_shadow(conn)
    param, current, shadow = _candidate_values(candidate)
    exp = create_shadow_experiment(
        conn,
        parameter=param,
        current_value=current,
        shadow_value=shadow,
        target_sample_size=SHADOW_TARGET_SAMPLE,
    )
    logger.info(
        "EVOLUTION_SHADOW | CREATED | id=%s | %s %.4f → %.4f",
        exp["id"],
        param,
        current,
        shadow,
    )
    return exp


def evaluate_trade_for_shadow(
    conn: sqlite3.Connection,
    trade: sqlite3.Row | dict[str, Any],
    *,
    shadow: dict[str, Any] | None = None,
) -> bool:
    """Record WOULD_ENTER / WOULD_SKIP for one closed paper trade."""
    running = shadow or get_running_shadow(conn)
    if running is None:
        return False

    trade_id = int(trade["id"])
    entry_price = float(trade["entry_price"])
    live_pnl = trade_pnl_percent(trade)
    decision = evaluate_shadow_decision_for_trade(
        trade,
        parameter=str(running["parameter"]),
        shadow_value=float(running["shadow_value"]),
        current_value=float(running["current_value"]),
    )
    shadow_pnl = live_pnl if decision == "WOULD_ENTER" else 0.0
    insert_shadow_evaluation(
        conn,
        shadow_id=int(running["id"]),
        trade_id=trade_id,
        market_slug=str(trade["market_slug"]),
        entry_price=entry_price,
        entry_ts=int(trade["entry_ts"]),
        shadow_decision=decision,
        live_pnl=live_pnl,
        shadow_pnl=shadow_pnl,
    )
    logger.debug(
        "EVOLUTION_SHADOW | %s | trade=%s | price=%.3f | live_pnl=%+.2f",
        decision,
        trade_id,
        entry_price,
        live_pnl,
    )
    return True


def sync_running_shadow_evaluations(conn: sqlite3.Connection) -> int:
    running = get_running_shadow(conn)
    if running is None:
        return 0
    count = 0
    for trade in fetch_unevaluated_trades(conn, shadow_id=int(running["id"])):
        if evaluate_trade_for_shadow(conn, trade, shadow=running):
            count += 1
    return count


def _compute_lane_metrics(pnls: list[float]) -> dict[str, float]:
    return lane_metrics(pnls)


def _verdict_from_metrics(live: dict[str, float], shadow: dict[str, float]) -> str:
    pf_ok = shadow["pf"] >= live["pf"] * (1.0 + SHADOW_PF_IMPROVEMENT_MIN)
    dd_ok = shadow["dd"] <= live["dd"]
    if pf_ok and dd_ok:
        return "PROMOTE"
    return "REJECT"


def maybe_finalize_shadow_experiment(conn: sqlite3.Connection) -> dict[str, Any] | None:
    running = get_running_shadow(conn)
    if running is None:
        return None
    target = int(running.get("target_sample_size") or SHADOW_TARGET_SAMPLE)
    sample_size = int(running.get("sample_size") or 0)
    if sample_size < target:
        return running

    evals = load_shadow_evaluations(conn, int(running["id"]))
    live_pnls = [float(e["live_pnl"]) for e in evals]
    shadow_pnls = [float(e["shadow_pnl"]) for e in evals]
    live = _compute_lane_metrics(live_pnls)
    shadow = _compute_lane_metrics(shadow_pnls)
    verdict = _verdict_from_metrics(live, shadow)
    complete_shadow_experiment(
        conn,
        shadow_id=int(running["id"]),
        shadow_pf=shadow["pf"],
        live_pf=live["pf"],
        shadow_wr=shadow["wr"],
        live_wr=live["wr"],
        shadow_dd=shadow["dd"],
        live_dd=live["dd"],
        verdict=verdict,
    )
    logger.info(
        "EVOLUTION_SHADOW | COMPLETE | id=%s | verdict=%s | live_pf=%.2f shadow_pf=%.2f",
        running["id"],
        verdict,
        live["pf"],
        shadow["pf"],
    )
    return get_latest_shadow(conn)


def sync_shadow_layer(
    conn: sqlite3.Connection,
    *,
    candidate: dict[str, Any] | None,
    ready_for_shadow: bool,
) -> dict[str, Any] | None:
    """Create experiment if ready, evaluate pending trades, finalize at target sample."""
    maybe_create_shadow_experiment(
        conn,
        candidate=candidate,
        ready_for_shadow=ready_for_shadow,
    )
    sync_running_shadow_evaluations(conn)
    return maybe_finalize_shadow_experiment(conn) or get_running_shadow(conn) or get_latest_shadow(conn)


def shadow_state_for_decision(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Map DB row to decision-engine shadow payload."""
    row = get_running_shadow(conn) or get_latest_shadow(conn)
    if row is None:
        return None

    status = str(row["status"]).upper()
    verdict = row.get("verdict")
    if status == "RUNNING":
        mapped_status = "RUNNING"
        reason = "Shadow experiment in progress — main strategy unchanged."
    elif verdict == "PROMOTE":
        mapped_status = "PROMOTE"
        reason = (
            f"Shadow complete — PROMOTE. Shadow PF {row.get('shadow_pf')} vs live {row.get('live_pf')}."
        )
    else:
        mapped_status = "REJECT"
        reason = (
            f"Shadow complete — REJECT. Shadow PF {row.get('shadow_pf')} vs live {row.get('live_pf')}."
        )

    target = int(row.get("target_sample_size") or SHADOW_TARGET_SAMPLE)
    sample = int(row.get("sample_size") or 0)
    return {
        "id": row["id"],
        "status": mapped_status,
        "reason": reason,
        "parameter": row["parameter"],
        "current_value": row["current_value"],
        "shadow_value": row["shadow_value"],
        "sample_size": sample,
        "target_sample_size": target,
        "progress_pct": round(100.0 * sample / target, 1) if target else 0.0,
        "candidate": {
            "parameter": row["parameter"],
            "from_value": row["current_value"],
            "to_value": row["shadow_value"],
        },
        "metrics": {
            "shadow_pf": row.get("shadow_pf"),
            "live_pf": row.get("live_pf"),
            "shadow_wr": row.get("shadow_wr"),
            "live_wr": row.get("live_wr"),
            "shadow_dd": row.get("shadow_dd"),
            "live_dd": row.get("live_dd"),
            "verdict": verdict,
        },
        "next_review_trades": max(0, target - sample) if status == "RUNNING" else None,
    }


def build_shadow_report_section(conn: sqlite3.Connection) -> dict[str, Any]:
    row = get_running_shadow(conn) or get_latest_shadow(conn)
    if row is None:
        return {"active": False, "experiments": []}
    evals = load_shadow_evaluations(conn, int(row["id"]))
    enter = sum(1 for e in evals if e["shadow_decision"] == "WOULD_ENTER")
    skip = sum(1 for e in evals if e["shadow_decision"] == "WOULD_SKIP")
    return {
        "active": row["status"] == "RUNNING",
        "experiment": dict(row),
        "decisions": {"WOULD_ENTER": enter, "WOULD_SKIP": skip},
        "evaluations": len(evals),
    }


# ---------------------------------------------------------------------------
# Persistent shadow state file
# ---------------------------------------------------------------------------

def save_shadow_state(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Save current shadow experiment state to reports/shadow_state.json + .md.

    Called automatically at the end of the daily pipeline.
    Returns the state dict or None if no experiment exists.
    """
    import json
    from pathlib import Path

    from bot.config import BASE_DIR

    row = get_running_shadow(conn) or get_latest_shadow(conn)
    if row is None:
        return None

    evals = load_shadow_evaluations(conn, int(row["id"]))
    live_pnls = [float(e["live_pnl"]) for e in evals]
    shadow_pnls = [float(e["shadow_pnl"]) for e in evals]

    live_m = _compute_lane_metrics(live_pnls) if live_pnls else {"pf": 0.0, "wr": 0.0, "dd": 0.0}
    shadow_m = _compute_lane_metrics(shadow_pnls) if shadow_pnls else {"pf": 0.0, "wr": 0.0, "dd": 0.0}

    target = int(row.get("target_sample_size") or SHADOW_TARGET_SAMPLE)
    sample = int(row.get("sample_size") or 0)

    state = {
        "shadow_id": row["id"],
        "parameter": row["parameter"],
        "current_value": row["current_value"],
        "shadow_value": row["shadow_value"],
        "status": row["status"],
        "started_at": row["created_at"],
        "completed_at": row.get("completed_at"),
        "progress": f"{sample} / {target}",
        "sample_size": sample,
        "target_sample_size": target,
        "pf_live": round(live_m["pf"], 3),
        "pf_shadow": round(shadow_m["pf"], 3),
        "wr_live": round(live_m["wr"], 1),
        "wr_shadow": round(shadow_m["wr"], 1),
        "dd_live": round(live_m["dd"], 2),
        "dd_shadow": round(shadow_m["dd"], 2),
        "verdict": row.get("verdict"),
    }

    reports_dir = BASE_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    json_path = reports_dir / "shadow_state.json"
    json_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    md_path = reports_dir / "shadow_state.md"
    md_path.write_text(_render_shadow_state_md(state), encoding="utf-8")

    logger.info("SHADOW_STATE | saved to %s", json_path)
    return state


def _render_shadow_state_md(state: dict[str, Any]) -> str:
    param_labels = {
        "entry_threshold": "Entry",
        "stop_loss": "Stop Loss",
        "trailing_activation": "Trailing Activation",
        "trailing_distance": "Trailing Distance",
    }
    param = param_labels.get(state["parameter"], state["parameter"])
    lines = [
        f"# Shadow #{state['shadow_id']}",
        "",
        f"**Parameter:** {param}",
        f"**Current:** {state['current_value']}",
        f"**Testing:** {state['shadow_value']}",
        f"**Started:** {state['started_at']}",
        f"**Progress:** {state['progress']}",
        "",
        f"**PF Live:** {state['pf_live']}",
        f"**PF Shadow:** {state['pf_shadow']}",
        f"**WR Live:** {state['wr_live']}%",
        f"**WR Shadow:** {state['wr_shadow']}%",
        f"**DD Live:** {state['dd_live']}%",
        f"**DD Shadow:** {state['dd_shadow']}%",
        "",
        f"**Status:** {state['status']}",
    ]
    if state.get("verdict"):
        lines.append(f"**Verdict:** {state['verdict']}")
    lines.append("")
    return "\n".join(lines)
