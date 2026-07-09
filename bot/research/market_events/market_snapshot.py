"""Event snapshot persistence around shocks."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.price_feed import SymbolPriceState


def persist_snapshot(
    conn: Any,
    *,
    event_id: int,
    snapshot_ts: int,
    offset_seconds: int,
    state: SymbolPriceState,
    event_price: float,
    extra: dict[str, Any] | None = None,
) -> int:
    price = state.last_price or event_price
    ret_from = (price / event_price - 1.0) * 100.0 if event_price > 0 else 0.0
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_event_snapshots (
          event_id, snapshot_ts, offset_seconds, price, return_from_event,
          volume, bid, ask, spread_bps, orderbook_imbalance,
          open_interest, funding, context_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, snapshot_ts, offset_seconds, price, ret_from,
            state.ticks[-1].volume if state.ticks else None,
            None, None, None, None, None, None,
            json.dumps(extra or {}),
        ),
    )
