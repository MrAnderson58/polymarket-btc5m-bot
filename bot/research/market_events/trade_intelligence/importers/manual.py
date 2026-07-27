"""Manual trade importer."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.trade_intelligence.knowledge import attach_default_layers
from bot.research.market_events.trade_intelligence.models import Outcome, TradeRecord
from bot.research.market_events.trade_intelligence.repository import TradeRepository


def import_manual_trade(
    conn: Any,
    *,
    symbol: str,
    side: str,
    entry_ts: int | None = None,
    exit_ts: int | None = None,
    entry_price: float | None = None,
    exit_price: float | None = None,
    size: float | None = None,
    pnl_usd: float | None = None,
    pnl_pct: float | None = None,
    strategy: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    external_id: str | None = None,
) -> int:
    repo = TradeRepository(conn)
    now = int(time.time())
    status = "closed" if exit_ts is not None else "open"
    ext = external_id or f"manual:{symbol.upper()}:{entry_ts or now}:{side.upper()}"
    rec = TradeRecord(
        id=None,
        source="manual",
        external_id=ext,
        symbol=symbol.upper(),
        side=side.upper(),
        entry_ts=entry_ts or now,
        exit_ts=exit_ts,
        entry_price=entry_price,
        exit_price=exit_price,
        size=size,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        strategy=strategy,
        status=status,
        raw_json={"imported_via": "manual"},
    )
    tag_list = ["manual", *(tags or [])]
    knowledge = attach_default_layers(repo, rec, tags=tag_list, note=note)
    tid = knowledge.trade.id
    assert tid is not None
    if status == "closed":
        result = "breakeven"
        if pnl_usd is not None:
            if pnl_usd > 1e-9:
                result = "win"
            elif pnl_usd < -1e-9:
                result = "loss"
        repo.upsert_outcome(
            Outcome(
                trade_id=tid,
                outcome_ts=exit_ts or now,
                result=result,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
            ),
        )
    return tid
