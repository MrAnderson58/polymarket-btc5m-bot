"""Daily pipeline orchestrator."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.ai_agent.learning import process_all_trades
from bot.optimizer.builder import save_optimizer_report
from bot.perf.feature_store import sync_trade_features_incremental
from bot.perf.timer import PerfReport
from bot.report.builder import build_report, save_report
from bot.report.memory import update_experiments
from bot.scientist.scheduler import run_scientist_cycle
from bot.strategy_review.builder import build_strategy_review
from bot.trading_brain.learning import run_brain_learning


def run_daily_pipeline(
    conn: sqlite3.Connection,
    *,
    quick_optimizer: bool = False,
    save_files: bool = True,
) -> dict[str, Any]:
    """
    Full daily compute chain (run once per day):
    Optimizer → Scientist → Brain → AI Agent → Strategy Review → Report (read-only assemble).
    """
    perf = PerfReport()
    summary: dict[str, Any] = {"steps": {}}

    with perf.step("Feature cache sync"):
        n, _ = sync_trade_features_incremental(conn)
        conn.commit()
        summary["features_synced"] = n

    with perf.step("Optimizer"):
        save_optimizer_report(conn, full_grid=not quick_optimizer)
        conn.commit()
        summary["optimizer"] = "ok"

    with perf.step("Scientist"):
        summary["scientist"] = run_scientist_cycle(conn)
        conn.commit()

    with perf.step("Trading Brain"):
        summary["brain"] = run_brain_learning(conn)
        conn.commit()

    with perf.step("AI Agent"):
        summary["ai_agent"] = process_all_trades(conn, run_brain=False)
        conn.commit()

    with perf.step("Trading Intelligence"):
        from bot.analytics.intelligence import save_trading_intelligence
        from bot.report.advanced import build_drift_detector
        from bot.report.analytics import build_entry_price_analysis, fetch_v2_trades

        closed = fetch_v2_trades(conn, closed_only=True)
        intel_report = {
            "entry_price_analysis": build_entry_price_analysis(closed),
            "drift_detector": build_drift_detector(closed),
        }
        save_trading_intelligence(conn, closed, intel_report)
        conn.commit()
        summary["intelligence"] = "ok"

    with perf.step("Strategy Review"):
        from bot.analytics.live_sample import build_live_sample
        from bot.optimizer.cache import sections_for_report
        from bot.report.analytics import fetch_v2_trades, trade_pnl, _metrics
        from bot.report.builder import _build_configuration
        from bot.report.memory import load_experiments
        from bot.scientist.builder import build_scientist_section
        from bot.trading_brain.report import build_brain_report

        closed = fetch_v2_trades(conn, closed_only=True)
        partial: dict[str, Any] = {
            "configuration": _build_configuration(),
            **sections_for_report(),
            "live_sample": build_live_sample(
                load_experiments(), _metrics([trade_pnl(t) for t in closed])["trades"]
            ),
            "scientist": build_scientist_section(conn, run_cycle=False),
            "trading_brain": build_brain_report(conn),
            "overfit_detector": {"level": "LOW", "overfit": False, "reasons": []},
        }
        summary["strategy_review"] = build_strategy_review(
            conn, partial, read_only=False
        )
        conn.commit()

    if save_files:
        with perf.step("Report (read-only assemble)"):
            cfg = partial.get("configuration", {})
            overall = partial.get("overall_performance", {})
            update_experiments(cfg, overall)
            paths = save_report(conn, read_only=True)
            summary["report_paths"] = [str(p) for p in paths]

    summary["perf"] = perf.as_dict()
    perf.print_report()

    with perf.step("Strategy Surgeon"):
        from bot.evolution.surgeon import render_surgeon_block, run_surgeon

        surgeon = run_surgeon(conn)
        print("\n" + render_surgeon_block(surgeon))
        summary["surgeon"] = surgeon

    with perf.step("Decision Council"):
        from bot.evolution.builder import build_evolution
        from bot.evolution.render import render_council_block, render_evolution_block
        from bot.evolution.shadow import save_shadow_state

        evolution = build_evolution(conn, surgeon=surgeon)
        print("\n" + render_council_block(evolution))
        print("\n" + render_evolution_block(evolution))
        summary["evolution"] = evolution
        summary["shadow_state"] = save_shadow_state(conn)
        conn.commit()

    with perf.step("Regime Shadow"):
        from bot.evolution.regime_shadow import (
            DEFAULT_FILTER_REGIMES,
            create_regime_shadow,
            get_running_regime_shadow,
            regime_shadow_state,
            render_regime_shadow_block,
            sync_regime_shadow,
        )

        if get_running_regime_shadow(conn) is None:
            create_regime_shadow(
                conn,
                filter_name="BTC Uptrend Filter",
                regimes=DEFAULT_FILTER_REGIMES,
            )
            conn.commit()
        sync_regime_shadow(conn)
        conn.commit()
        rs_state = regime_shadow_state(conn)
        block = render_regime_shadow_block(rs_state)
        if block:
            print("\n" + block)
        summary["regime_shadow"] = rs_state

    with perf.step("Evolution History"):
        from bot.evolution.history import (
            load_history,
            render_history_block,
            save_history_file,
            sync_history_from_shadows,
        )

        sync_history_from_shadows(conn)
        conn.commit()
        history = load_history(conn)
        block = render_history_block(history)
        if block:
            print("\n" + block)
        save_history_file(conn)
        summary["evolution_history"] = history

    return summary
