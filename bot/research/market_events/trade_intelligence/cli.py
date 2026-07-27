"""CLI handlers for `trade import|list|report|similar`."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.trade_intelligence.importers.csv_import import import_csv_trades
from bot.research.market_events.trade_intelligence.importers.manual import import_manual_trade
from bot.research.market_events.trade_intelligence.importers.paper import import_paper_trades
from bot.research.market_events.trade_intelligence.importers.telegram import import_telegram_stub
from bot.research.market_events.trade_intelligence.reports import (
    format_similar_trades,
    format_trade_list,
    format_trade_report,
)
from bot.research.market_events.trade_intelligence.repository import TradeRepository
from bot.research.market_events.trade_intelligence.schema import ensure_trade_intelligence_schema

TRADE_ACTIONS = ("import", "list", "report", "similar")


def run_trade_cli(
    conn: Any,
    *,
    action: str,
    source: str | None = None,
    csv_path: str | None = None,
    trade_id: int | None = None,
    symbol: str | None = None,
    side: str | None = None,
    limit: int = 50,
    note: str | None = None,
) -> str:
    ensure_trade_intelligence_schema(conn)
    repo = TradeRepository(conn)
    act = (action or "").strip().lower()

    if act == "import":
        src = (source or "paper").strip().lower()
        if src == "paper":
            stats = import_paper_trades(conn, limit=limit if limit > 0 else None)
            return (
                "TRADE IMPORT — paper\n"
                f"created={stats['created']} updated={stats['updated']} total={stats['total']}"
            )
        if src == "csv":
            if not csv_path:
                return "trade import --source csv requires --csv PATH"
            stats = import_csv_trades(conn, csv_path)
            return (
                "TRADE IMPORT — csv\n"
                f"created={stats['created']} updated={stats['updated']} total={stats['total']}"
            )
        if src == "manual":
            if not symbol or not side:
                return "trade import --source manual requires --symbols SYMBOL and --side SIDE"
            # --symbols may be comma list; take first
            sym = symbol.split(",")[0].strip()
            tid = import_manual_trade(
                conn,
                symbol=sym,
                side=side,
                note=note,
            )
            return f"TRADE IMPORT — manual\ntrade_id={tid}"
        if src == "telegram":
            stats = import_telegram_stub(conn)
            return (
                "TRADE IMPORT — telegram\n"
                f"status={stats.get('status')} message={stats.get('message')}"
            )
        return f"Unknown import source: {src} (paper|csv|manual|telegram)"

    if act == "list":
        return format_trade_list(repo, source=source, limit=limit)

    if act == "report":
        return format_trade_report(repo, trade_id=trade_id)

    if act == "similar":
        if trade_id is None:
            return "trade similar requires --trade-id ID"
        return format_similar_trades(repo, trade_id=trade_id, limit=limit)

    return (
        "Usage:\n"
        "  trade import [--source paper|csv|manual|telegram] [--csv PATH]\n"
        "  trade list [--source SRC] [--limit N]\n"
        "  trade report [--trade-id ID]\n"
        "  trade similar --trade-id ID [--limit N]"
    )
