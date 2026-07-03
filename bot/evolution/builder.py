"""Assemble evolution decision from existing caches and tables (read-only)."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.ai_agent.daily import build_daily_report
from bot.analytics.live_sample import build_live_sample
from bot.evolution.council import CouncilResult, convene_council
from bot.evolution.shadow import (
    build_shadow_report_section,
    shadow_state_for_decision,
    sync_shadow_layer,
)
from bot.evolution.state import EvolutionStatus
from bot.optimizer.cache import load_optimizer_cache
from bot.report.analytics import fetch_v2_trades, trade_pnl, _metrics
from bot.report.memory import load_experiments as load_memory_experiments
from bot.scientist.builder import build_scientist_section
from bot.scientist.experiments import load_experiments as load_scientist_experiments
from bot.scientist.scheduler import _best_next_step
from bot.strategy_review.cache import load_strategy_review_cache
from bot.trading_brain.report import build_brain_report


def load_evolution_sources(conn: sqlite3.Connection) -> dict[str, Any]:
    """Load all inputs without running optimizer/replay/scientist cycles."""
    closed = fetch_v2_trades(conn, closed_only=True)
    total_trades = _metrics([trade_pnl(t) for t in closed])["trades"]
    scientist = build_scientist_section(conn, run_cycle=False)
    scientist["best_next_step"] = _best_next_step(
        load_scientist_experiments(conn, limit=200),
        int(total_trades),
    )
    return {
        "optimizer": load_optimizer_cache(),
        "strategy_review": load_strategy_review_cache(),
        "scientist": scientist,
        "trading_brain": build_brain_report(conn),
        "ai_agent": build_daily_report(conn),
        "live_sample": build_live_sample(load_memory_experiments(), total_trades),
    }


def build_evolution(
    conn: sqlite3.Connection,
    surgeon: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build evolution via Decision Council.

    The Council collects votes from all 6 AI sources, finds consensus,
    and returns ONE final decision.
    """
    sources = load_evolution_sources(conn)

    council = convene_council(sources, surgeon=surgeon)

    candidate = council.as_candidate()
    ready = council.status == EvolutionStatus.READY_FOR_SHADOW.value

    sync_shadow_layer(
        conn,
        candidate=candidate,
        ready_for_shadow=ready,
    )
    conn.commit()

    shadow = shadow_state_for_decision(conn)
    if shadow:
        council = convene_council(sources, surgeon=surgeon, shadow_state=shadow)

    result = _council_to_evolution_result(council)
    result["shadow_report"] = build_shadow_report_section(conn)
    result["council"] = _serialize_council(council)
    return result


def _council_to_evolution_result(council: CouncilResult) -> dict[str, Any]:
    """Convert CouncilResult to the legacy evolution result dict."""
    from bot.evolution.constants import EVOLUTION_VERSION

    candidate = council.as_candidate()
    return {
        "version": EVOLUTION_VERSION,
        "status": council.status,
        "reason": council.reason,
        "next_review_trades": None,
        "candidate": candidate,
        "evidence": {
            "trades": council.evidence_trades,
            "expected_pf_pct": council.expected_pf_pct,
        },
        "watch_reasons": [],
        "sources_meta": {},
    }


def _serialize_council(council: CouncilResult) -> dict[str, Any]:
    """Serialize council for report/CLI rendering."""
    return {
        "status": council.status,
        "final_parameter": council.final_parameter,
        "final_from_value": council.final_from_value,
        "final_to_value": council.final_to_value,
        "final_label": council.final_label,
        "confidence_pct": council.confidence_pct,
        "reason": council.reason,
        "votes": [
            {
                "source": v.source,
                "label": v.label,
                "parameter": v.parameter,
                "value": v.value,
                "is_keep": v.is_keep,
                "reason": v.reason,
            }
            for v in council.votes
        ],
        "evidence_trades": council.evidence_trades,
        "expected_pf_pct": council.expected_pf_pct,
    }
