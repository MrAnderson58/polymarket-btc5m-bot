"""Version comparison (Block 10)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bot.config import BASE_DIR
from bot.performance import build_version_summaries, fetch_closed_trades


def _load_previous_optimizer() -> dict[str, Any] | None:
    index = BASE_DIR / "optimizer_reports" / "index.json"
    if not index.exists():
        return None
    entries = json.loads(index.read_text(encoding="utf-8"))
    if len(entries) < 2:
        return None
    prev = entries[-2]
    path = BASE_DIR / "optimizer_reports" / prev["json_path"]
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_version_manager(
    conn: sqlite3.Connection,
    *,
    current_report: dict[str, Any],
) -> dict[str, Any]:
    all_trades = fetch_closed_trades(conn)
    summaries = build_version_summaries(all_trades)
    versions = [
        {
            "version": s.version,
            "trades": s.trades,
            "win_rate": s.win_rate,
            "profit_factor": s.profit_factor,
            "avg_pnl": s.average_pnl_percent,
            "max_drawdown_pct": s.max_drawdown_percent,
        }
        for s in summaries
    ]

    live = current_report.get("parameter_optimizer", {}).get("current", {})
    replay = current_report.get("parameter_optimizer", {}).get("optimal", {})
    prev = _load_previous_optimizer()
    prev_opt = (prev or {}).get("parameter_optimizer", {}).get("optimal", {})

    comparisons: list[dict[str, Any]] = []
    if prev_opt:
        delta_pf = replay.get("profit_factor", 0) - prev_opt.get("profit_factor", 0)
        comparisons.append(
            {
                "pair": "Current Live vs Previous Optimizer",
                "verdict": _verdict(delta_pf),
                "delta_pf": delta_pf,
            }
        )
    live_pf = live.get("profit_factor", 0)
    replay_pf = replay.get("profit_factor", 0)
    comparisons.append(
        {
            "pair": "Current Live vs Replay Optimal",
            "verdict": _verdict(replay_pf - live_pf),
            "delta_pf": replay_pf - live_pf,
        }
    )

    v2 = next((v for v in versions if v["version"] == "V2"), None)
    v4 = next((v for v in versions if "V4" in v["version"]), None)
    if v2 and v4:
        comparisons.append(
            {
                "pair": "V2 Live vs V4 Shadow",
                "verdict": _verdict(v4["profit_factor"] - v2["profit_factor"]),
                "delta_pf": v4["profit_factor"] - v2["profit_factor"],
            }
        )

    return {"versions": versions, "comparisons": comparisons}


def _verdict(delta: float) -> str:
    if delta > 0.15:
        return "Лучше"
    if delta < -0.15:
        return "Хуже"
    return "Без изменений"
