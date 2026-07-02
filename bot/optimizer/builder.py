"""Assemble Trading AI Optimizer v1 report."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR
from bot.optimizer.analysis import discover_rules, run_cluster_analysis
from bot.optimizer.dataset import (
    build_replay_contexts,
    load_trade_features,
    rebuild_trade_features,
)
from bot.optimizer.grid import run_grid_search
from bot.optimizer.ml import train_models
from bot.optimizer.recommendations import build_recommendations
from bot.optimizer.replay import StrategyParams
from bot.optimizer.versions import build_version_manager
from bot.optimizer.walk_forward import run_walk_forward


def _optimal_params(grid: dict[str, Any]) -> StrategyParams:
    o = grid["optimal"]
    return StrategyParams(
        max_entry=float(o["entry"]),
        stop_pct=float(o["stop_pct"]),
        trailing_activation=float(o["trailing_activation"]),
        trailing_distance=float(o["trailing_distance"]),
        time_stop_sec=int(o["time_stop_sec"]),
        btc_filter_usd=float(o["btc_filter_usd"]),
    )


def build_optimizer_report(conn, *, full_grid: bool = True) -> dict[str, Any]:
    started = time.time()
    feature_count = rebuild_trade_features(conn)
    conn.commit()
    rows = load_trade_features(conn)
    replays = build_replay_contexts(conn)

    grid = run_grid_search(replays, max_combos=None if full_grid else 5000)
    optimal = _optimal_params(grid)
    walk_forward = run_walk_forward(replays, optimal)
    ml = train_models(rows)
    clusters = run_cluster_analysis(rows)
    rules_stop = discover_rules(rows, target="is_stop")
    rules_win = discover_rules(rows, target="is_win")

    report: dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "optimizer_version": "1.0",
            "runtime_sec": round(time.time() - started, 1),
            "trade_features_count": feature_count,
            "replay_trades": len(replays),
        },
        "dataset": {"rows": feature_count, "source": "early_reversion_v2_trades"},
        "parameter_optimizer": grid,
        "machine_learning": ml,
        "cluster_analysis": clusters,
        "rules_discovery": rules_stop + rules_win,
        "walk_forward": walk_forward,
    }
    report["version_manager"] = build_version_manager(conn, current_report=report)
    report["recommendations"] = build_recommendations(report)
    return report


def _attach_report_sections(conn, report: dict[str, Any]) -> dict[str, Any]:
    """Add heatmaps/sensitivity and derived analytics for unified cache (daily pipeline)."""
    from bot.analytics.heatmap import build_heatmaps
    from bot.analytics.overfit import build_overfit_detector
    from bot.analytics.sensitivity import build_sensitivity_analysis
    from bot.analytics.stability import build_parameter_stability
    from bot.optimizer.cache import normalize_walk_forward
    from bot.report.advanced import build_equity_curve, build_monte_carlo
    from bot.report.analytics import fetch_v2_trades

    closed = fetch_v2_trades(conn, closed_only=True)
    optimizer = report.get("parameter_optimizer", {})
    report["heatmaps"] = build_heatmaps(conn, closed, optimizer=optimizer)
    report["sensitivity_analysis"] = build_sensitivity_analysis(conn, closed, optimizer)
    report["walk_forward"] = normalize_walk_forward(report.get("walk_forward", []))
    report["monte_carlo"] = build_monte_carlo(closed)
    report["equity_curve"] = build_equity_curve(closed)
    report["parameter_stability"] = build_parameter_stability(report)
    report["overfit_detector"] = build_overfit_detector(report, closed)
    return report


def save_optimizer_report(conn, *, full_grid: bool = True) -> tuple[Path, Path]:
    from bot.optimizer.cache import save_optimizer_cache
    from bot.optimizer.render import render_json, render_markdown

    report = build_optimizer_report(conn, full_grid=full_grid)
    report = _attach_report_sections(conn, report)
    save_optimizer_cache(report)
    out_dir = BASE_DIR / "optimizer_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
    md_path = out_dir / f"optimizer_{stamp}.md"
    json_path = out_dir / f"optimizer_{stamp}.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")

    day_dir = out_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    day_md = day_dir / md_path.name
    day_json = day_dir / json_path.name
    day_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    day_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

    index_path = out_dir / "index.json"
    index: list[dict[str, Any]] = []
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    index.append(
        {
            "generated_at": report["meta"]["generated_at"],
            "md_path": md_path.name,
            "json_path": json_path.name,
            "optimal_pf": report["parameter_optimizer"]["optimal"].get("profit_factor"),
            "recommendations_count": len(report["recommendations"]),
        }
    )
    index_path.write_text(json.dumps(index[-90:], indent=2), encoding="utf-8")
    return md_path, json_path
