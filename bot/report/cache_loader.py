"""Load precomputed sections for read-only Report assembly."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.ai_agent.daily import build_daily_report
from bot.optimizer.cache import sections_for_report
from bot.optimizer.dataset import load_trade_features
from bot.report.advanced import build_correlation_matrix, build_feature_importance
from bot.scientist.builder import build_scientist_section
from bot.strategy_review.cache import load_strategy_review_cache
from bot.trading_brain.report import build_brain_report


def _features_from_cache(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = load_trade_features(conn)
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "pnl": float(row["pnl"] or 0),
                "btc_move_30s": row["btc_move_30s"],
                "entry_price": float(row["entry_price"]),
                "holding_sec": float(row["holding_time"] or 0),
                "volatility": row["volatility_30s"],
                "spread": row["spread"],
                "slippage": 0.0,
            }
        )
    return out


def load_brain_section(conn: sqlite3.Connection) -> dict[str, Any]:
    return build_brain_report(conn)


def load_scientist_section(conn: sqlite3.Connection) -> dict[str, Any]:
    return build_scientist_section(conn, run_cycle=False)


def load_strategy_review_section() -> dict[str, Any]:
    return load_strategy_review_cache()


def load_ai_agent_section(conn: sqlite3.Connection) -> dict[str, Any]:
    return build_daily_report(conn)


def load_ai_sections(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "ai_agent": load_ai_agent_section(conn),
        "trading_brain": load_brain_section(conn),
        "scientist": load_scientist_section(conn),
        "strategy_review": load_strategy_review_section(),
    }


def load_optimizer_sections() -> dict[str, Any]:
    return sections_for_report()


def load_feature_analytics(conn: sqlite3.Connection) -> dict[str, Any]:
    features = _features_from_cache(conn)
    if not features:
        return {"correlation_matrix": {}, "feature_importance": []}
    return {
        "correlation_matrix": build_correlation_matrix(features),
        "feature_importance": build_feature_importance(features),
    }


def build_heatmaps_readonly(closed: list, report: dict[str, Any]) -> dict[str, Any]:
    """Entry/holding heatmaps without stop/trail replay."""
    from bot.analytics.heatmap import _heatmap_rows, enrich_heatmaps_from_report
    from bot.report.analytics import ENTRY_PRICES, HOLDING_BUCKETS_SEC, _metrics, trade_pnl

    entry_items = []
    for price in ENTRY_PRICES:
        subset = [t for t in closed if abs(float(t["entry_price"]) - price) <= 0.005]
        pnls = [trade_pnl(t) for t in subset]
        entry_items.append({"entry": f"{price:.2f}", "entry_price": price, **_metrics(pnls)})

    holding_items = []
    for max_sec in HOLDING_BUCKETS_SEC:
        subset = [t for t in closed if float(t["holding_time_seconds"] or 0) <= max_sec]
        pnls = [trade_pnl(t) for t in subset]
        holding_items.append({"holding": f"<={max_sec}s", "max_sec": max_sec, **_metrics(pnls)})

    heatmaps = {
        "entry_threshold": _heatmap_rows(entry_items, value_key="entry_price", label_key="entry"),
        "holding_time": _heatmap_rows(holding_items, value_key="max_sec", label_key="holding"),
        "stop_loss": [],
        "trailing": [],
        "btc_filter": [],
        "note": "Stop/trailing heatmaps from optimizer_cache (run daily pipeline)",
    }
    cached = report.get("heatmaps") or {}
    if cached.get("stop_loss"):
        heatmaps["stop_loss"] = cached["stop_loss"]
    if cached.get("trailing"):
        heatmaps["trailing"] = cached["trailing"]
    return enrich_heatmaps_from_report(heatmaps, report)
