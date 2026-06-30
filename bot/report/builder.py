"""Assemble the full analytics report (v3)."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.analytics.heatmap import build_heatmaps, enrich_heatmaps_from_report
from bot.analytics.intelligence import build_trading_intelligence
from bot.analytics.live_sample import build_live_sample
from bot.analytics.overfit import build_overfit_detector
from bot.analytics.research_notebook import build_research_notebook
from bot.analytics.sensitivity import build_sensitivity_analysis
from bot.analytics.stability import build_parameter_stability
from bot.config import (
    ER_ENTRY_PRICE_OFFSET,
    effective_entry_threshold,
)
import bot.config as config
from bot.er_stats import _enabled_strategy_names
from bot.er_stop_loss_short_recovery_stats import fetch_stop_loss_short_recovery_stats
from bot.er_trailing_stats import fetch_trailing_strategy_stats
from bot.recovery_health import fetch_recovery_health_stats
from bot.recommendations.action_plan import build_final_action_plan
from bot.recommendations.safe_to_change import build_safe_to_change
from bot.report.analytics import (
    build_alternative_stop_test,
    build_btc_direction_extended,
    build_btc_filter_analysis,
    build_bot_health,
    build_entry_price_analysis,
    build_execution_quality,
    build_exit_analysis,
    build_funnel_extended,
    build_holding_time_analysis,
    build_mae_mfe,
    build_market_regime,
    build_open_positions,
    build_stop_loss_analysis,
    build_trailing_simulation,
    build_walk_forward,
    fetch_v2_trades,
    trade_pnl,
    _metrics,
)
from bot.report.advanced import (
    build_correlation_matrix,
    build_drift_detector,
    build_equity_curve,
    build_feature_importance,
    build_live_vs_replay,
    build_monte_carlo,
    build_parameter_optimizer,
    build_trade_features,
)
from bot.report.decision_engine import build_decision_engine
from bot.report.memory import (
    append_memory_index,
    archive_day_copies,
    build_version_comparison,
    create_report_bundle,
    load_experiments,
    update_experiments,
    write_ai_notes,
)
from bot.report.recommendations import generate_recommendations
from bot.report.render import write_report_files
from bot.report.scoring import compute_scores
from bot.v4_shadow_stats import fetch_v4_shadow_summary
from bot.yes_c_shadow_stats import fetch_no_c_live_summary, fetch_yes_c_shadow_summary


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _build_configuration() -> dict[str, Any]:
    return {
        "git_commit": _git_commit(),
        "version": "v2",
        "trading_mode": config.TRADING_MODE,
        "enabled_strategies_v2": sorted(config.ENABLED_STRATEGIES_V2),
        "yes_c_shadow": config.ENABLE_YES_C_SHADOW,
        "v4_shadow": config.ENABLE_V4_SHADOW,
        "no_c_filter_shadow": config.ENABLE_NO_C_FILTER_SHADOW,
        "btc_filter_live": {
            "normal_threshold": effective_entry_threshold(0.40),
            "strict_threshold": effective_entry_threshold(0.36),
            "btc_move_strict_usd": 5.0,
        },
        "entry_offset": ER_ENTRY_PRICE_OFFSET,
        "trailing_activation": config.TRAILING_ACTIVATION_PROFIT,
        "trailing_distance": config.TRAILING_OFFSET,
        "stop_loss_pct": config.ER_V2_STOP_LOSS_PCT,
        "time_stop_sec": config.ER_V2_TIME_STOP_SEC,
        "position_size_usdc": config.EARLY_REVERSION_POSITION_SIZE_USDC,
        "max_open_positions": config.MAX_OPEN_POSITIONS,
        "max_daily_loss_usdc": config.MAX_DAILY_LOSS_USDC,
        "poll_interval_sec": config.POLL_INTERVAL_SEC,
        "exit_mode": config.EXIT_MODE,
    }


def _summary_to_shadow_dict(summary) -> dict[str, Any]:
    return {
        "trades": summary.trades,
        "win_rate": summary.win_rate,
        "avg_pnl": summary.avg_pnl_percent,
        "profit_factor": summary.profit_factor,
        "net_profit": summary.avg_pnl_percent * summary.trades,
    }


def build_report(conn) -> dict[str, Any]:
    now_ts = int(time.time())
    generated_at = datetime.now(timezone.utc).isoformat()
    closed = fetch_v2_trades(conn, closed_only=True)
    closed_pnls = [trade_pnl(t) for t in closed]
    overall = _metrics(closed_pnls)
    existing_experiments = load_experiments()

    short_stats = fetch_stop_loss_short_recovery_stats(conn)
    short_dict = {
        "total_stop_loss": short_stats.total_stop_loss,
        "analyzed": short_stats.analyzed,
        "skipped_no_checks": short_stats.skipped_no_checks,
        "recovered_entry_15s_count": short_stats.recovered_entry_15s_count,
        "recovered_entry_15s_rate": short_stats.recovered_entry_15s_rate,
        "recovered_entry_30s_count": short_stats.recovered_entry_30s_count,
        "recovered_entry_30s_rate": short_stats.recovered_entry_30s_rate,
        "recovered_entry_45s_count": short_stats.recovered_entry_45s_count,
        "recovered_entry_45s_rate": short_stats.recovered_entry_45s_rate,
        "recovered_entry_60s_count": short_stats.recovered_entry_60s_count,
        "recovered_entry_60s_rate": short_stats.recovered_entry_60s_rate,
        "recovered_entry_plus_001_60s_count": short_stats.recovered_entry_plus_001_60s_count,
        "recovered_entry_plus_001_60s_rate": short_stats.recovered_entry_plus_001_60s_rate,
        "recovered_entry_plus_002_60s_count": short_stats.recovered_entry_plus_002_60s_count,
        "recovered_entry_plus_002_60s_rate": short_stats.recovered_entry_plus_002_60s_rate,
        "recovered_trailing_60s_count": short_stats.recovered_trailing_60s_count,
        "recovered_trailing_60s_rate": short_stats.recovered_trailing_60s_rate,
        "never_recovered_60s_count": short_stats.never_recovered_60s_count,
        "never_recovered_60s_rate": short_stats.never_recovered_60s_rate,
        "alternative_holds": [
            {
                "hold_sec": h.hold_sec,
                "trades": h.trades,
                "win_rate": h.win_rate,
                "avg_pnl": h.avg_pnl,
                "profit_factor": h.profit_factor,
                "net_profit": h.net_profit,
            }
            for h in short_stats.alternative_holds
        ],
    }

    trailing_by_strategy = {}
    for strategy in _enabled_strategy_names() or ("NO_C", "YES_B"):
        ts = fetch_trailing_strategy_stats(conn, strategy)
        trailing_by_strategy[strategy] = {
            "entries": ts.entries,
            "trailing_activated": ts.trailing_activated,
            "activation_rate": ts.activation_rate,
            "avg_peak_before_exit": ts.avg_peak_before_exit,
            "avg_peak_after_activation": ts.avg_peak_after_activation,
            "avg_profit_at_activation": ts.avg_profit_at_activation,
            "exited_by_trailing": ts.exited_by_trailing,
            "exited_by_stop_loss": ts.exited_by_stop_loss,
        }

    recovery_health = fetch_recovery_health_stats(conn, now_ts=now_ts)
    positions = build_open_positions(conn, now_ts=now_ts)
    positions["open_count"] = len(positions["positions"])

    shadow: dict[str, Any] = {}
    try:
        yes_c = fetch_yes_c_shadow_summary(conn)
        no_c_live = fetch_no_c_live_summary(conn)
        v4 = fetch_v4_shadow_summary(conn)
        shadow["YES_C"] = _summary_to_shadow_dict(yes_c)
        shadow["NO_C_live"] = _summary_to_shadow_dict(no_c_live)
        shadow["V4"] = {
            "trades": v4.closed_trades,
            "win_rate": v4.win_rate,
            "avg_pnl": v4.avg_pnl_percent,
            "profit_factor": v4.profit_factor,
            "net_profit": v4.avg_pnl_percent * v4.closed_trades,
        }
    except Exception as exc:
        shadow["note"] = f"Shadow stats partially unavailable: {exc}"

    report: dict[str, Any] = {
        "meta": {
            "generated_at": generated_at,
            "generated_at_ts": now_ts,
            "report_version": "4.0",
        },
        "configuration": _build_configuration(),
        "bot_health": build_bot_health(conn, now_ts=now_ts),
        "recovery_health": {
            "open_trades": recovery_health.open_trades,
            "expired_open_trades": recovery_health.expired_open_trades,
            "failed_exit_intents": recovery_health.failed_exit_intents,
            "blocked_max_open_positions_1h": recovery_health.blocked_max_open_positions_1h,
            "recovery_actions_1h": recovery_health.recovery_actions_1h,
        },
        "current_positions": positions,
        "entry_funnel": build_funnel_extended(conn),
        "overall_performance": overall,
        "entry_price_analysis": build_entry_price_analysis(closed),
        "exit_analysis": build_exit_analysis(closed),
        "stop_loss_analysis": build_stop_loss_analysis(closed),
        "stop_loss_short_recovery": short_dict,
        "alternative_stop_test": build_alternative_stop_test(conn, closed),
        "trailing_analysis": trailing_by_strategy,
        "trailing_simulation": build_trailing_simulation(conn, closed),
        "btc_direction": build_btc_direction_extended(conn),
        "btc_filter_analysis": build_btc_filter_analysis(conn, closed),
        "shadow_strategies": shadow,
        "execution_quality": build_execution_quality(conn),
        "holding_time_analysis": build_holding_time_analysis(closed),
        "mae_mfe": build_mae_mfe(conn, closed),
        "market_regime": build_market_regime(conn, closed),
        "walk_forward": build_walk_forward(closed),
    }

    cfg = report["configuration"]
    trade_features = build_trade_features(conn, closed)
    optimizer = build_parameter_optimizer(
        conn,
        closed,
        current_entry=cfg.get("btc_filter_live", {}).get("normal_threshold", 0.40),
        current_stop_pct=cfg["stop_loss_pct"],
        current_trailing=cfg["trailing_activation"],
    )
    report["parameter_optimizer"] = optimizer
    report["correlation_matrix"] = build_correlation_matrix(trade_features)
    report["feature_importance"] = build_feature_importance(trade_features)
    report["monte_carlo"] = build_monte_carlo(closed)
    report["equity_curve"] = build_equity_curve(closed)
    report["drift_detector"] = build_drift_detector(closed)
    report["live_vs_optimal"] = build_live_vs_replay(overall, optimizer)
    report["version_comparison"] = build_version_comparison(conn)

    report["live_sample"] = build_live_sample(existing_experiments, overall["trades"])
    report["parameter_stability"] = build_parameter_stability(report)
    report["overfit_detector"] = build_overfit_detector(report, closed)
    report["heatmaps"] = enrich_heatmaps_from_report(
        build_heatmaps(conn, closed, optimizer), report
    )
    report["sensitivity_analysis"] = build_sensitivity_analysis(conn, closed, optimizer)
    report["safe_to_change"] = build_safe_to_change(report)

    report["recommendations"] = generate_recommendations(report)
    report["final_score"] = compute_scores(report)
    report["ai_decision"] = build_decision_engine(report)
    report["final_action_plan"] = build_final_action_plan(report)
    report.update(build_trading_intelligence(conn, closed, report))
    report["research_notebook"] = build_research_notebook(report)
    report["experiments"] = update_experiments(cfg, overall)
    return report


def save_report(conn) -> tuple[Path, Path, Path]:
    from bot.config import BASE_DIR

    report = build_report(conn)
    stamp = datetime.now(timezone.utc)
    stamp_str = stamp.strftime("%Y-%m-%d_%H-%M")
    reports_dir = BASE_DIR / "reports"
    md_path = reports_dir / f"report_{stamp_str}.md"
    json_path = reports_dir / f"report_{stamp_str}.json"
    report["ai_memory"] = append_memory_index(
        md_path=md_path, json_path=json_path, report=report
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    from bot.report.render import render_json, render_markdown

    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")
    day_dir = archive_day_copies(md_path=md_path, json_path=json_path, stamp=stamp)
    ai_notes_path = write_ai_notes(report, day_dir=day_dir)
    zip_path = create_report_bundle(
        md_path=md_path,
        json_path=json_path,
        ai_notes_path=ai_notes_path,
        stamp=stamp,
    )
    return md_path, json_path, zip_path
