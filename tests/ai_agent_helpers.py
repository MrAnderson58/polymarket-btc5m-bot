"""Shared test helpers for AI Agent v2."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from bot.database import (
    close_early_reversion_v2_trade,
    insert_early_reversion_v2_trade,
)


def seed_trades(conn, count: int = 3) -> list[int]:
    now = int(time.time())
    ids: list[int] = []
    for i in range(count):
        entry = 0.36 + i * 0.01
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug=f"btc-updown-5m-agent-{i}",
            window_start_ts=now - 120 - i * 10,
            end_ts=now + 180,
            side="NO",
            strategy_name="NO_C",
            entry_price=entry,
            entry_ts=now - 60 - i * 10,
        )
        pnl = 10.0 if i % 2 == 0 else -8.0
        close_early_reversion_v2_trade(
            conn,
            trade_id,
            exit_price=entry + (0.04 if pnl > 0 else -0.03),
            exit_reason="TRAILING_STOP" if pnl > 0 else "STOP_LOSS",
            pnl_percent=pnl,
            pnl_usdc=pnl * 0.02,
            holding_time_seconds=30.0 + i * 5,
        )
        ids.append(trade_id)
        for offset, bid in ((0, entry), (20, entry + 0.02), (40, entry + (0.04 if pnl > 0 else -0.02))):
            conn.execute(
                """
                INSERT INTO market_checks (
                    market_slug, seconds_remaining, strike_price, btc_price,
                    yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime(?, 'unixepoch'))
                """,
                (
                    f"btc-updown-5m-agent-{i}",
                    200.0 - offset,
                    100_000.0,
                    100_000.0 - offset - i,
                    0.5,
                    0.51,
                    bid,
                    bid + 0.01,
                    None,
                    now - 60 - i * 10 + offset,
                ),
            )
    return ids
