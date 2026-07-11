"""Orchestrate E.4 historical replay pipeline (research tables only)."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.historical_replay.ai_critic import run_ai_critic_shadow
from bot.research.market_events.historical_replay.constants import REPLAY_RUN_TAG_DEFAULT
from bot.research.market_events.historical_replay.context_linker import link_replay_context
from bot.research.market_events.historical_replay.shock_replay import run_shock_replay
from bot.research.market_events.historical_replay.strategy_matrix import run_strategy_matrix


def run_full_replay_pipeline(
    conn: Any,
    *,
    run_tag: str = REPLAY_RUN_TAG_DEFAULT,
    days: int | None = 30,
    symbols: list[str] | None = None,
) -> dict[str, int]:
    """Run shock replay → context link → strategy matrix → AI critic shadow."""
    stats = run_shock_replay(conn, run_tag=run_tag, days=days, symbols=symbols)
    stats["context_links"] = link_replay_context(conn, run_tag=run_tag)
    sm = run_strategy_matrix(conn, run_tag=run_tag)
    stats["strategy_results"] = sm.get("results", 0)
    stats["ai_critic"] = run_ai_critic_shadow(conn, run_tag=run_tag)
    return stats
