"""Regime Shadow — counterfactual filter experiment.

Tests: what would happen if NO_C did NOT enter during certain regimes
(e.g. Strong Uptrend, News Spike).

For each trade in the filtered regime:
  - If it was a loss → "saved_loss" (filter would have helped)
  - If it was a profit → "missed_profit" (filter would have hurt)

After target_sample_size observations → PROMOTE_FILTER or REJECT_FILTER.

No changes to execution. Read-only statistics.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from bot.evolution.constants import SHADOW_TARGET_SAMPLE
from bot.evolution.metrics import lane_metrics

logger = logging.getLogger(__name__)

DEFAULT_FILTER_REGIMES = ("Strong Uptrend", "News Spike")
REGIME_SHADOW_TARGET = SHADOW_TARGET_SAMPLE


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

def get_running_regime_shadow(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM evolution_regime_shadow
        WHERE status = 'RUNNING'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def get_latest_regime_shadow(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM evolution_regime_shadow
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def create_regime_shadow(
    conn: sqlite3.Connection,
    *,
    filter_name: str,
    regimes: tuple[str, ...] | list[str],
    target_sample_size: int = REGIME_SHADOW_TARGET,
) -> dict[str, Any]:
    """Create a new regime filter shadow experiment."""
    if get_running_regime_shadow(conn) is not None:
        return get_running_regime_shadow(conn)  # type: ignore

    cur = conn.execute(
        """
        INSERT INTO evolution_regime_shadow (
            filter_name, regimes_json, status, created_at, target_sample_size
        ) VALUES (?, ?, 'RUNNING', ?, ?)
        """,
        (filter_name, json.dumps(list(regimes)), _utc_now(), target_sample_size),
    )
    row = conn.execute(
        "SELECT * FROM evolution_regime_shadow WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    logger.info(
        "REGIME_SHADOW | CREATED | id=%s | filter=%s | regimes=%s",
        row["id"], filter_name, regimes,
    )
    return dict(row)


def _fetch_unevaluated_trades(
    conn: sqlite3.Connection,
    regime_shadow_id: int,
) -> list[dict[str, Any]]:
    """Get closed trades with regime labels not yet evaluated for this experiment."""
    rows = conn.execute(
        """
        SELECT t.id AS trade_id, t.pnl_percent, t.entry_price, t.exit_price,
               t.entry_ts, t.closed_at, f.regime_label
        FROM early_reversion_v2_trades t
        JOIN trade_features f ON f.trade_id = t.id
        LEFT JOIN evolution_regime_shadow_trades e
            ON e.regime_shadow_id = ? AND e.trade_id = t.id
        JOIN evolution_regime_shadow s ON s.id = ?
        WHERE t.status = 'closed'
          AND t.closed_at >= s.created_at
          AND e.id IS NULL
          AND f.regime_label IS NOT NULL
        ORDER BY t.entry_ts ASC
        """,
        (regime_shadow_id, regime_shadow_id),
    ).fetchall()
    return [dict(r) for r in rows]


def evaluate_trade_for_regime_shadow(
    conn: sqlite3.Connection,
    trade: dict[str, Any],
    *,
    regime_shadow_id: int,
    filter_regimes: list[str],
) -> str:
    """Evaluate a single trade against the regime filter.

    Returns: 'saved_loss', 'missed_profit', or 'normal'.
    """
    regime = str(trade.get("regime_label", ""))
    pnl = float(trade.get("pnl_percent") or 0.0)
    in_filter = regime in filter_regimes

    if in_filter:
        outcome = "saved_loss" if pnl < 0 else "missed_profit"
    else:
        outcome = "normal"

    conn.execute(
        """
        INSERT OR IGNORE INTO evolution_regime_shadow_trades (
            regime_shadow_id, trade_id, regime_label, in_filter, pnl_percent, outcome, evaluated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (regime_shadow_id, trade["trade_id"], regime, int(in_filter), pnl, outcome, _utc_now()),
    )
    return outcome


def sync_regime_shadow(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Evaluate pending trades and update counters. Finalize if target reached."""
    running = get_running_regime_shadow(conn)
    if running is None:
        return None

    shadow_id = int(running["id"])
    filter_regimes = json.loads(running["regimes_json"])
    trades = _fetch_unevaluated_trades(conn, shadow_id)

    for trade in trades:
        evaluate_trade_for_regime_shadow(
            conn, trade, regime_shadow_id=shadow_id, filter_regimes=filter_regimes
        )

    _update_counters(conn, shadow_id)

    running = get_running_regime_shadow(conn)
    if running and int(running["sample_size"] or 0) >= int(running["target_sample_size"] or REGIME_SHADOW_TARGET):
        _finalize_regime_shadow(conn, shadow_id)
        return get_latest_regime_shadow(conn)

    return running


def _update_counters(conn: sqlite3.Connection, shadow_id: int) -> None:
    stats = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN in_filter = 1 THEN 1 ELSE 0 END), 0) AS skipped,
            COALESCE(SUM(CASE WHEN outcome = 'saved_loss' THEN 1 ELSE 0 END), 0) AS saved_losses,
            COALESCE(SUM(CASE WHEN outcome = 'missed_profit' THEN 1 ELSE 0 END), 0) AS missed_winners,
            COALESCE(SUM(CASE WHEN outcome = 'saved_loss' THEN abs(pnl_percent) ELSE 0 END), 0.0) AS saved_loss_pnl,
            COALESCE(SUM(CASE WHEN outcome = 'missed_profit' THEN pnl_percent ELSE 0 END), 0.0) AS missed_profit_pnl
        FROM evolution_regime_shadow_trades
        WHERE regime_shadow_id = ?
        """,
        (shadow_id,),
    ).fetchone()

    conn.execute(
        """
        UPDATE evolution_regime_shadow
        SET sample_size = ?,
            skipped = ?,
            saved_losses = ?,
            missed_winners = ?,
            saved_loss_pnl = ?,
            missed_profit_pnl = ?
        WHERE id = ?
        """,
        (
            int(stats["total"] or 0),
            int(stats["skipped"] or 0),
            int(stats["saved_losses"] or 0),
            int(stats["missed_winners"] or 0),
            float(stats["saved_loss_pnl"] or 0.0),
            float(stats["missed_profit_pnl"] or 0.0),
            shadow_id,
        ),
    )


def _finalize_regime_shadow(conn: sqlite3.Connection, shadow_id: int) -> None:
    """Compute final verdict and mark complete."""
    all_trades = conn.execute(
        "SELECT pnl_percent, in_filter FROM evolution_regime_shadow_trades WHERE regime_shadow_id = ?",
        (shadow_id,),
    ).fetchall()

    live_pnls = [float(r["pnl_percent"]) for r in all_trades]
    filtered_pnls = [float(r["pnl_percent"]) for r in all_trades if not r["in_filter"]]

    live_m = lane_metrics(live_pnls) if live_pnls else {"pf": 0.0}
    filtered_m = lane_metrics(filtered_pnls) if filtered_pnls else {"pf": 0.0}

    if live_m["pf"] > 0:
        improvement_pct = round((filtered_m["pf"] - live_m["pf"]) / live_m["pf"] * 100, 1)
    else:
        improvement_pct = 100.0 if filtered_m["pf"] > 0 else 0.0

    verdict = "PROMOTE_FILTER" if improvement_pct >= 10.0 else "REJECT_FILTER"

    conn.execute(
        """
        UPDATE evolution_regime_shadow
        SET status = 'COMPLETE',
            completed_at = ?,
            net_pf_improvement_pct = ?,
            verdict = ?
        WHERE id = ?
        """,
        (_utc_now(), improvement_pct, verdict, shadow_id),
    )
    logger.info(
        "REGIME_SHADOW | COMPLETE | id=%s | verdict=%s | pf_improvement=%.1f%%",
        shadow_id, verdict, improvement_pct,
    )


# ---------------------------------------------------------------------------
# State for rendering
# ---------------------------------------------------------------------------

def regime_shadow_state(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = get_running_regime_shadow(conn) or get_latest_regime_shadow(conn)
    if row is None:
        return None

    # Count historical eligible trades (for diagnostics)
    eligible = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM early_reversion_v2_trades t
        JOIN trade_features f ON f.trade_id = t.id
        WHERE t.status = 'closed'
          AND f.regime_label IS NOT NULL
          AND t.closed_at >= ?
        """,
        (row["created_at"],),
    ).fetchone()

    return {
        "id": row["id"],
        "filter_name": row["filter_name"],
        "regimes": json.loads(row["regimes_json"]),
        "status": row["status"],
        "sample_size": int(row["sample_size"] or 0),
        "target_sample_size": int(row["target_sample_size"] or REGIME_SHADOW_TARGET),
        "skipped": int(row["skipped"] or 0),
        "saved_losses": int(row["saved_losses"] or 0),
        "missed_winners": int(row["missed_winners"] or 0),
        "saved_loss_pnl": float(row["saved_loss_pnl"] or 0.0),
        "missed_profit_pnl": float(row["missed_profit_pnl"] or 0.0),
        "net_pf_improvement_pct": row.get("net_pf_improvement_pct"),
        "verdict": row.get("verdict"),
        "started_at": row["created_at"],
        "eligible_trades": int(eligible["n"]) if eligible else 0,
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_regime_shadow_block(state: dict[str, Any] | None) -> str:
    if state is None:
        return ""

    lines = [
        "----------------------------------",
        "",
        "REGIME SHADOW",
        "",
        f"  Filter: {state['filter_name']}",
        f"  Regimes: {', '.join(state['regimes'])}",
        f"  Progress: {state['sample_size']} / {state['target_sample_size']}",
        "",
        f"  Skipped: {state['skipped']}",
        f"  Saved losses: {state['saved_losses']}",
        f"  Missed winners: {state['missed_winners']}",
    ]

    if state.get("net_pf_improvement_pct") is not None:
        lines.append(f"  Net PF improvement: {state['net_pf_improvement_pct']:+.0f}%")

    lines.append("")
    if state.get("verdict"):
        lines.append(f"  Recommendation: {state['verdict']}")
    else:
        lines.append(f"  Status: {state['status']}")

    lines.extend(["", "----------------------------------", ""])
    return "\n".join(lines)


def render_regime_shadow_section(state: dict[str, Any] | None) -> list[str]:
    if state is None:
        return ["", "_No regime shadow experiment._", ""]

    lines = [
        "",
        f"**Filter:** {state['filter_name']}",
        f"**Regimes:** {', '.join(state['regimes'])}",
        f"**Status:** {state['status']}",
        f"**Progress:** {state['sample_size']} / {state['target_sample_size']}",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Skipped | {state['skipped']} |",
        f"| Saved losses | {state['saved_losses']} |",
        f"| Missed winners | {state['missed_winners']} |",
        f"| Saved loss PnL | {state['saved_loss_pnl']:.1f}% |",
        f"| Missed profit PnL | {state['missed_profit_pnl']:.1f}% |",
    ]

    if state.get("net_pf_improvement_pct") is not None:
        lines.append(f"| Net PF improvement | {state['net_pf_improvement_pct']:+.1f}% |")

    lines.append("")
    if state.get("verdict"):
        lines.append(f"**Recommendation:** {state['verdict']}")
    lines.append("")
    lines.append("_Counterfactual only — main strategy unchanged._")
    return lines
