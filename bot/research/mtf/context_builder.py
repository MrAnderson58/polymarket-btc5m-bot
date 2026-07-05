"""Build full trade context for historical analysis."""

from __future__ import annotations

import sqlite3

from bot.research.bidirectional_live_audit import LiveTrade, dedupe_first_trade_per_market, load_closed_trades
from bot.research.mtf.btc_context import build_btc_context
from bot.research.mtf.labels import label_trade_context
from bot.research.mtf.models import TradeContext
from bot.research.mtf.polymarket_context import load_pm_context_at_ts


def build_trade_context(conn: sqlite3.Connection, trade: LiveTrade) -> TradeContext:
    ts = trade.entry_ts
    btc = build_btc_context(conn, ts)
    ctx = TradeContext(
        trade_id=trade.id,
        market_slug=trade.market_slug,
        entry_ts=ts,
        side=trade.side,
        entry_price=trade.entry_price,
        pnl_pct=trade.pnl_pct,
        btc=btc,
        pm_15m=load_pm_context_at_ts(conn, ts, "15m"),
        pm_1h=load_pm_context_at_ts(conn, ts, "1h"),
        pm_daily=load_pm_context_at_ts(conn, ts, "daily"),
    )
    return label_trade_context(ctx)


def load_corrected_trade_contexts(conn: sqlite3.Connection) -> list[TradeContext]:
    raw = load_closed_trades(conn)
    trades, _ = dedupe_first_trade_per_market(raw)
    return [build_trade_context(conn, t) for t in trades]
