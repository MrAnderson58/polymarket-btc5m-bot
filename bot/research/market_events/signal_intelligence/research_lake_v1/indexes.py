"""Ensure JOIN-key indexes for Research Lake streaming builds."""

from __future__ import annotations

from typing import Any

# (name, ddl) — CREATE INDEX IF NOT EXISTS only.
_JOIN_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "idx_s55_paper_trade_id",
        "CREATE INDEX IF NOT EXISTS idx_s55_paper_trade_id "
        "ON market_events_trade_features_s55(paper_trade_id)",
    ),
    (
        "idx_s56_paper_trade_id",
        "CREATE INDEX IF NOT EXISTS idx_s56_paper_trade_id "
        "ON market_events_trade_snapshots_s56(paper_trade_id)",
    ),
    (
        "idx_s42_status_id",
        "CREATE INDEX IF NOT EXISTS idx_s42_status_id "
        "ON market_events_paper_trades_s42(status, id)",
    ),
    (
        "idx_s42_closed_pnl_id",
        "CREATE INDEX IF NOT EXISTS idx_s42_closed_pnl_id "
        "ON market_events_paper_trades_s42(status, id) "
        "WHERE status='CLOSED' AND pnl_pct IS NOT NULL",
    ),
    (
        "idx_g31_symbol_created",
        "CREATE INDEX IF NOT EXISTS idx_g31_symbol_created "
        "ON market_candidate_g31(symbol, created_at)",
    ),
    (
        "idx_rlake_v1_row_hash",
        "CREATE INDEX IF NOT EXISTS idx_rlake_v1_row_hash "
        "ON market_events_research_lake_v1(trade_id, row_hash)",
    ),
    (
        "idx_alpha_v2_trade_id",
        "CREATE INDEX IF NOT EXISTS idx_alpha_v2_trade_id "
        "ON market_events_alpha_validations_v2(trade_id)",
    ),
    (
        "idx_alpha_v2_paper_trade_id",
        "CREATE INDEX IF NOT EXISTS idx_alpha_v2_paper_trade_id "
        "ON market_events_alpha_validations_v2(paper_trade_id)",
    ),
    (
        "idx_alpha_v1_trade_id",
        "CREATE INDEX IF NOT EXISTS idx_alpha_v1_trade_id "
        "ON market_events_alpha_labels_v1(trade_id)",
    ),
)


def ensure_research_lake_join_indexes(conn: Any) -> list[str]:
    """Create missing JOIN indexes. Returns list of index names ensured."""
    ensured: list[str] = []
    for name, ddl in _JOIN_INDEXES:
        try:
            conn.execute(ddl)
            ensured.append(name)
        except Exception:
            # Table may not exist (S56/G31 optional) — skip silently.
            pass
    try:
        conn.commit()
    except Exception:
        pass
    return ensured


__all__ = ["ensure_research_lake_join_indexes"]
