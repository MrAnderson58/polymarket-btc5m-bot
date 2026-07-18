"""PositionsService — open paper trades from existing S4.2 table (read-only)."""

from __future__ import annotations

from typing import Any, Protocol

from bot.terminal.models.dto import PositionCard
from bot.terminal.services._db import market_events_ro


class PositionService(Protocol):
    def get_open(self) -> list[PositionCard]:
        ...


def _f(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class DefaultPositionService:
    def get_open(self) -> list[PositionCard]:
        try:
            from bot.research.market_events.signal_intelligence.candles import load_recent_candles
            from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
                CAPITAL_PER_TRADE_USD,
                LEVERAGE,
                STATUS_OPEN,
            )

            with market_events_ro() as conn:
                rows = conn.execute(
                    """
                    SELECT symbol, direction, entry, stop, capital_usd, leverage, status
                    FROM market_events_paper_trades_s42
                    WHERE status = ?
                    ORDER BY created_at DESC
                    """,
                    (STATUS_OPEN,),
                ).fetchall()

                cards: list[PositionCard] = []
                for row in rows:
                    symbol = str(row["symbol"] or "?")
                    side = str(row["direction"] or "?")
                    entry = _f(row["entry"])
                    stop = _f(row["stop"])
                    size = _f(row["capital_usd"]) or float(CAPITAL_PER_TRADE_USD)
                    risk = None
                    if entry is not None and stop is not None:
                        risk = abs(entry - stop)
                    pnl_pct = None
                    pnl_usd = None
                    try:
                        bars = load_recent_candles(
                            conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=2,
                        )
                        if bars and entry and entry > 0:
                            price = float(bars[-1].close)
                            is_long = side.upper() == "LONG"
                            if is_long:
                                pnl_pct = (price / entry - 1.0) * 100.0
                            else:
                                pnl_pct = (1.0 - price / entry) * 100.0
                            lev = _f(row["leverage"]) or float(LEVERAGE)
                            pnl_usd = round(size * (pnl_pct / 100.0) * lev, 2)
                            pnl_pct = round(pnl_pct, 2)
                    except Exception:
                        pass
                    cards.append(
                        PositionCard(
                            symbol=symbol,
                            side=side,
                            size=size,
                            entry=entry,
                            stop=stop,
                            unrealized_pnl=pnl_usd,
                            unrealized_pnl_pct=pnl_pct,
                            risk=risk,
                            status="open",
                            extra={"source": "market_events_paper_trades_s42"},
                        )
                    )
                return cards
        except Exception as exc:
            return [
                PositionCard(
                    symbol="—",
                    side="—",
                    status="unavailable",
                    extra={"error": str(exc), "source": "fallback"},
                )
            ]

    def list_positions(self) -> list[PositionCard]:
        return self.get_open()


def get_position_service() -> PositionService:
    return DefaultPositionService()


__all__ = ["DefaultPositionService", "PositionService", "get_position_service"]
