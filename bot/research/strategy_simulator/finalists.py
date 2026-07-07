"""Finalist qualification and shadow candidate export (observe-only)."""

from __future__ import annotations

import sqlite3

from bot.research.strategy_simulator.storage import (
    SHADOW_CANDIDATES_TABLE,
    enable_shadow_candidate,
    list_shadow_candidates,
    store_shadow_candidates,
)
from bot.research.strategy_simulator.walk_forward import WalkForwardResult


def export_qualified_finalists(
    conn: sqlite3.Connection,
    results: list[WalkForwardResult],
    *,
    replace: bool = True,
) -> list[int]:
    """Export only qualified strategies to ss_shadow_candidates."""
    qualified = [r for r in results if r.qualified]
    return store_shadow_candidates(conn, qualified, replace=replace)


def list_finalists(conn: sqlite3.Connection) -> list[dict]:
    return list_shadow_candidates(conn)


def shadow_enable(conn: sqlite3.Connection, strategy_id: int, *, enabled: bool = True) -> None:
    enable_shadow_candidate(conn, strategy_id, enabled=enabled)
