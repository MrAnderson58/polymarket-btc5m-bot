"""Import closed shock-paper runs into Trade Intelligence (read-only on paper tables)."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.performance import (
    DEFAULT_NOTIONAL_USD,
    load_completed_trades,
)
from bot.research.market_events.trade_intelligence.knowledge import attach_default_layers
from bot.research.market_events.trade_intelligence.models import (
    ContextItem,
    Outcome,
    TradeRecord,
)
from bot.research.market_events.trade_intelligence.repository import TradeRepository


def import_paper_trades(
    conn: Any,
    *,
    notional_usd: float | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Upsert completed paper_strategy_runs into ti_trades + outcome/context."""
    repo = TradeRepository(conn)
    notional = float(notional_usd if notional_usd is not None else DEFAULT_NOTIONAL_USD)
    trades = load_completed_trades(conn, notional=notional)
    if limit is not None:
        trades = trades[-max(0, int(limit)) :] if limit else trades

    created = 0
    updated = 0
    for t in trades:
        external_id = f"paper_run:{t.run_id}"
        existing = conn.execute(
            "SELECT id FROM ti_trades WHERE source = ? AND external_id = ?",
            ("paper", external_id),
        ).fetchone()
        rec = TradeRecord(
            id=None,
            source="paper",
            external_id=external_id,
            symbol=t.symbol,
            side=t.side,
            entry_ts=t.entry_ts,
            exit_ts=t.exit_ts,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            size=notional,
            pnl_usd=t.pnl_usd,
            pnl_pct=t.net_return_pct,
            strategy=t.strategy_name,
            status="closed",
            raw_json={
                "event_id": t.event_id,
                "run_id": t.run_id,
                "reversal_variant": t.reversal_variant,
                "exit_variant": t.exit_variant,
                "exit_reason": t.exit_reason,
                "gross_return_pct": t.gross_return_pct,
                "net_return_pct": t.net_return_pct,
                "shock_direction": t.shock_direction,
            },
        )
        knowledge = attach_default_layers(
            repo,
            rec,
            tags=["paper", t.reversal_variant.lower(), t.exit_variant.lower()],
        )
        tid = knowledge.trade.id
        assert tid is not None
        result = "breakeven"
        if t.pnl_usd > 1e-9:
            result = "win"
        elif t.pnl_usd < -1e-9:
            result = "loss"
        repo.upsert_outcome(
            Outcome(
                trade_id=tid,
                outcome_ts=t.exit_ts,
                result=result,
                pnl_usd=t.pnl_usd,
                pnl_pct=t.net_return_pct,
                exit_reason=t.exit_reason,
                payload={"duration_sec": t.duration_sec},
            ),
        )
        repo.add_context(
            ContextItem(
                trade_id=tid,
                context_key="event_id",
                context_value=str(t.event_id),
            ),
        )
        repo.add_context(
            ContextItem(
                trade_id=tid,
                context_key="shock_direction",
                context_value=t.shock_direction,
            ),
        )
        if existing:
            updated += 1
        else:
            created += 1
    return {"created": created, "updated": updated, "total": len(trades)}
