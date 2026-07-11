"""Task E — fixed user strategy matrix S1–S5 on replay shocks."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP
from bot.research.market_events.historical_replay.constants import (
    FEE_BPS_SCENARIOS,
    REPLAY_STRATEGIES,
    SLIPPAGE_BPS_SCENARIOS,
)
from bot.research.market_events.historical_replay.splits import split_bucket_for_ts


def _fade_direction(shock_direction: str) -> str:
    return SHOCK_DIRECTION_DOWN if shock_direction == SHOCK_DIRECTION_UP else SHOCK_DIRECTION_UP


def _simulate_strategy(
    *,
    strategy: dict,
    shock_ret: float,
    path_rows: list[Any],
) -> dict[str, Any]:
    """Deterministic simplified simulation using path metrics at 300s horizon."""
    fade = _fade_direction("UP" if shock_ret > 0 else "DOWN")
    h300 = next((r for r in path_rows if int(r["horizon_sec"]) == 300), None)
    if not h300:
        return {"entered": 0, "reason": "no_path_data"}

    reclaim = json.loads(h300["raw_json"] or "{}").get("reclaim_pct_of_shock", 0)
    mfe = float(h300["mfe_fade_pct"] or 0)
    mae = float(h300["mae_fade_pct"] or 0)
    sid = strategy["id"]

    if strategy.get("delayed_confirm") and reclaim < strategy.get("confirm_reclaim_pct", 50):
        return {"entered": 0, "reason": "delayed_confirm_not_met"}

    stop = float(strategy.get("stop_pct", 1.0))
    tp = float(strategy.get("tp_pct", strategy.get("tp1_pct", 0.5)))
    if mae <= -stop:
        gross = -stop
        exit_reason = "STOP"
    elif mfe >= tp:
        gross = tp
        exit_reason = "TP"
    elif sid == "S2_TP_TO_BE" and mfe >= float(strategy.get("be_trigger_pct", 0.3)):
        gross = 0.0
        exit_reason = "BE"
    else:
        gross = float(h300["reversal_pct"] or 0)
        exit_reason = "TIME"

    return {
        "entered": 1,
        "gross_return_pct": gross,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "exit_reason": exit_reason,
    }


def run_strategy_matrix(conn: Any, *, run_tag: str) -> dict[str, int]:
    run = conn.execute(
        "SELECT id FROM market_events_replay_runs WHERE run_tag = ?",
        (run_tag,),
    ).fetchone()
    if not run:
        return {"strategies": 0}
    run_id = int(run["id"])
    shocks = conn.execute(
        "SELECT id, event_ts, direction, impulse_pct, symbol, asset_class, session_regime FROM market_events_replay_shocks WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    stats = {"strategies": 0, "results": 0}
    for sh in shocks:
        paths = conn.execute(
            "SELECT * FROM market_events_replay_path_metrics WHERE shock_id = ?",
            (sh["id"],),
        ).fetchall()
        bucket = split_bucket_for_ts(conn, run_tag=run_tag, event_ts=int(sh["event_ts"]))
        for strat in REPLAY_STRATEGIES:
            sim = _simulate_strategy(
                strategy=strat, shock_ret=float(sh["impulse_pct"]), path_rows=paths,
            )
            for fee in FEE_BPS_SCENARIOS:
                for slip in SLIPPAGE_BPS_SCENARIOS:
                    if not sim.get("entered"):
                        continue
                    gross = float(sim["gross_return_pct"])
                    net = gross - (fee + slip) / 100.0
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO market_events_replay_strategy_results (
                          run_id, shock_id, strategy_id, split_bucket, entered,
                          gross_return_pct, net_return_pct, mfe_pct, mae_pct,
                          exit_reason, fee_bps, slippage_bps, raw_json
                        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id, sh["id"], strat["id"], bucket,
                            gross, net, sim.get("mfe_pct"), sim.get("mae_pct"),
                            sim.get("exit_reason"), fee, slip,
                            json.dumps({"symbol": sh["symbol"], "asset_class": sh["asset_class"]}),
                        ),
                    )
                    stats["results"] += 1
            stats["strategies"] += 1
    return stats


def strategy_matrix_report(conn: Any, *, run_tag: str) -> str:
    rows = conn.execute(
        """
        SELECT strategy_id, split_bucket, COUNT(*) AS n,
               AVG(net_return_pct) AS avg_ret,
               SUM(CASE WHEN net_return_pct > 0 THEN 1 ELSE 0 END) AS wins
        FROM market_events_replay_strategy_results r
        JOIN market_events_replay_runs u ON u.id = r.run_id
        WHERE u.run_tag = ?
        GROUP BY strategy_id, split_bucket
        ORDER BY strategy_id, split_bucket
        """,
        (run_tag,),
    ).fetchall()
    lines = [
        "HISTORICAL STRATEGY MATRIX",
        f"run_tag: {run_tag}",
        "mode: HISTORICAL REPLAY — not PAPER ONLINE or LIVE",
        "",
    ]
    if not rows:
        lines.append("No strategy results. Run historical-shock-replay then historical-strategy-matrix.")
        return "\n".join(lines)
    for r in rows:
        n = int(r["n"])
        wr = 100.0 * int(r["wins"]) / n if n else 0
        lines.append(
            f"  {r['strategy_id']} [{r['split_bucket']}]: n={n} win_rate={wr:.0f}% avg_net={r['avg_ret']:.3f}%",
        )
    split = conn.execute(
        "SELECT * FROM market_events_replay_splits WHERE run_tag = ?",
        (run_tag,),
    ).fetchone()
    if split:
        lines.extend([
            "",
            "Split boundaries (persisted before evaluation):",
            f"  train_end_ts: {split['train_end_ts']}",
            f"  validation_end_ts: {split['validation_end_ts']}",
            f"  holdout_end_ts: {split['holdout_end_ts']}",
            "Do not optimize thresholds on holdout.",
        ])
    else:
        lines.append("")
        lines.append("VERDICT: INSUFFICIENT_DATA — no persisted split.")
    return "\n".join(lines)
