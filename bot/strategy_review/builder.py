"""Assemble Strategy Review from SQLite + existing report data."""

from __future__ import annotations

import sqlite3
from typing import Any

import bot.config as config
from bot.config import effective_entry_threshold
from bot.report.analytics import fetch_v2_trades
from bot.strategy_review.combinations import analyze_combinations
from bot.strategy_review.constants import REVIEW_VERSION, SAFETY_GATE
from bot.strategy_review.entry import analyze_entry
from bot.strategy_review.stop_loss import analyze_stop
from bot.strategy_review.trailing import analyze_trailing
from bot.strategy_review.verdict import build_final_verdict


def _read_context(report: dict[str, Any]) -> dict[str, Any]:
    cfg = report.get("configuration", {})
    return {
        "current_entry": effective_entry_threshold(0.40),
        "current_stop_pct": float(cfg.get("stop_loss_pct", config.ER_V2_STOP_LOSS_PCT)),
        "current_activation": float(cfg.get("trailing_activation", config.TRAILING_ACTIVATION_PROFIT)),
        "current_distance": float(cfg.get("trailing_distance", config.TRAILING_OFFSET)),
        "optimizer": report.get("parameter_optimizer", {}),
        "walk_forward": report.get("walk_forward", {}),
        "overfit": report.get("overfit_detector", {}),
        "scientist": report.get("scientist", {}),
        "trading_brain": report.get("trading_brain", {}),
        "live_sample": report.get("live_sample", {}),
    }


def build_strategy_review(
    conn: sqlite3.Connection,
    report: dict[str, Any],
    *,
    read_only: bool = False,
) -> dict[str, Any]:
    """Review using optimizer cache — compute only in daily pipeline."""
    from bot.optimizer.cache import load_optimizer_cache
    from bot.strategy_review.cache import load_strategy_review_cache, save_strategy_review_cache

    if read_only:
        return load_strategy_review_cache()

    if not report.get("parameter_optimizer"):
        opt_cache = load_optimizer_cache()
        report = {
            **report,
            "parameter_optimizer": opt_cache.get("parameter_optimizer", {}),
            "walk_forward": opt_cache.get("walk_forward", {}),
        }

    closed = fetch_v2_trades(conn, closed_only=True)
    ctx = _read_context(report)

    entry = analyze_entry(conn, closed=closed, current_entry=ctx["current_entry"])
    stop = analyze_stop(conn, closed=closed, current_stop_pct=ctx["current_stop_pct"])
    trailing = analyze_trailing(
        conn,
        closed=closed,
        current_activation=ctx["current_activation"],
        current_distance=ctx["current_distance"],
    )
    combinations = analyze_combinations(
        conn,
        closed=closed,
        entry_analysis=entry,
        stop_analysis=stop,
        trailing_analysis=trailing,
    )
    final_verdict = build_final_verdict(report, entry=entry, stop=stop, trailing=trailing)

    result = {
        "version": REVIEW_VERSION,
        "mode": "observe_only",
        "disclaimer": (
            "Strategy Review recommends at most ONE parameter change. "
            "Human approval required. Nothing is applied automatically."
        ),
        "safety_gate": SAFETY_GATE,
        "context": ctx,
        "entry_analysis": entry,
        "stop_loss_analysis": stop,
        "trailing_analysis": trailing,
        "combinations": combinations,
        "final_verdict": final_verdict,
    }
    save_strategy_review_cache(result)
    return result
