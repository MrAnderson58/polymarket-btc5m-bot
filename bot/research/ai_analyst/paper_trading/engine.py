"""S47 — Paper Trading Engine with multi-target scale-out."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

from bot.research.ai_analyst.paper_trading.models import (
    EXIT_MANUAL,
    EXIT_STOP,
    EXIT_TP1,
    EXIT_TP2,
    EXIT_TP3,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_PENDING,
    TP1_FRACTION,
    TP2_FRACTION,
    TP3_FRACTION,
    Fill,
    PaperTrade,
    compute_stats,
    price_pnl_pct,
    price_to_r,
    sl_hit,
    tp_hit,
)
from bot.research.ai_analyst.paper_trading.signals import (
    TradingSignal,
    fill_entry_price,
    price_in_entry_range,
)

logger = logging.getLogger(__name__)

DEFAULT_EQUITY = 10_000.0


class PaperTradingEngine:
    """
    Lifecycle:
      PENDING signal → price enters Entry range → OPEN trade
      OPEN → scale out TP1/TP2/TP3 or Stop on remaining size
    """

    def __init__(
        self,
        *,
        initial_equity: float = DEFAULT_EQUITY,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.initial_equity = float(initial_equity)
        self.equity = float(initial_equity)
        self.signals: dict[str, TradingSignal] = {}
        self.trades: dict[str, PaperTrade] = {}
        self._on_event = on_event

    def _emit(self, event: dict[str, Any]) -> None:
        if self._on_event:
            self._on_event(event)

    def submit_signal(self, signal: TradingSignal) -> TradingSignal:
        errs = signal.validate()
        if errs:
            raise ValueError("; ".join(errs))
        signal.status = STATUS_PENDING
        self.signals[signal.signal_id] = signal
        self._emit({"type": "signal_submitted", "signal_id": signal.signal_id})
        return signal

    def open_trades(self) -> list[PaperTrade]:
        return [t for t in self.trades.values() if t.status == STATUS_OPEN]

    def closed_trades(self) -> list[PaperTrade]:
        return [t for t in self.trades.values() if t.status == STATUS_CLOSED]

    def pending_signals(self) -> list[TradingSignal]:
        return [s for s in self.signals.values() if s.status == STATUS_PENDING]

    def stats(self, *, strategy: str | None = None) -> dict[str, Any]:
        trades = list(self.trades.values())
        if strategy:
            trades = [t for t in trades if t.strategy == strategy]
        base = compute_stats(trades)
        base["open_trades"] = sum(1 for t in trades if t.status == STATUS_OPEN)
        base["equity"] = round(self.equity, 4)
        base["strategy"] = strategy or "ALL"
        return base

    def stats_by_strategy(self) -> dict[str, dict[str, Any]]:
        strategies = sorted({t.strategy for t in self.trades.values()})
        return {s: self.stats(strategy=s) for s in strategies}

    def tick(self, symbol: str, price: float, *, ts: int | None = None) -> list[dict[str, Any]]:
        """Process one price update for symbol — open pending / manage open."""
        now = int(ts if ts is not None else time.time())
        symbol = str(symbol).upper()
        price = float(price)
        events: list[dict[str, Any]] = []

        for sig in list(self.pending_signals()):
            if sig.symbol != symbol:
                continue
            if price_in_entry_range(price, sig):
                trade = self._open_from_signal(sig, price=price, now=now)
                ev = {"type": "opened", "trade_id": trade.trade_id, "entry": trade.entry}
                events.append(ev)
                self._emit(ev)

        for trade in list(self.open_trades()):
            if trade.symbol != symbol:
                continue
            events.extend(self._manage_open(trade, price=price, now=now))

        return events

    def _open_from_signal(self, signal: TradingSignal, *, price: float, now: int) -> PaperTrade:
        entry = fill_entry_price(price, signal)
        trade = PaperTrade(
            trade_id=f"t47-{uuid.uuid4().hex[:12]}",
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            direction=signal.direction,
            strategy=signal.strategy,
            entry=entry,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
            risk_pct=signal.risk_pct,
            confidence=signal.confidence,
            reasons=list(signal.reasons),
            opened_at=now,
            status=STATUS_OPEN,
            account_equity_at_open=self.equity,
        )
        signal.status = "TRIGGERED"
        self.trades[trade.trade_id] = trade
        return trade

    def _manage_open(self, trade: PaperTrade, *, price: float, now: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        is_long = trade.direction == "LONG"
        pnl_pct = price_pnl_pct(trade.entry, price, is_long=is_long)
        r_now = price_to_r(trade.entry, price, trade.stop_loss, is_long=is_long)
        trade.mfe_pct = max(trade.mfe_pct, pnl_pct)
        trade.mae_pct = min(trade.mae_pct, pnl_pct)
        trade.mfe_r = max(trade.mfe_r, r_now)
        trade.mae_r = min(trade.mae_r, r_now)
        trade.holding_seconds = max(0, now - trade.opened_at)

        # Stop first on remaining size
        if sl_hit(is_long, price, trade.stop_loss) and trade.size_remaining > 1e-9:
            fill = self._apply_fill(
                trade,
                level=EXIT_STOP,
                price=trade.stop_loss,
                fraction=trade.size_remaining,
                now=now,
            )
            events.append({"type": "fill", "level": EXIT_STOP, "trade_id": trade.trade_id, "fill": fill})
            self._finalize_if_flat(trade, exit_reason=EXIT_STOP, exit_price=trade.stop_loss, now=now)
            return events

        # Scale-out targets in order
        if not trade.tp1_hit and tp_hit(is_long, price, trade.tp1):
            frac = min(TP1_FRACTION, trade.size_remaining)
            fill = self._apply_fill(trade, level=EXIT_TP1, price=trade.tp1, fraction=frac, now=now)
            trade.tp1_hit = True
            events.append({"type": "fill", "level": EXIT_TP1, "trade_id": trade.trade_id, "fill": fill})
            if trade.size_remaining <= 1e-9:
                self._finalize_if_flat(trade, exit_reason=EXIT_TP1, exit_price=trade.tp1, now=now)
                return events

        if trade.tp1_hit and not trade.tp2_hit and tp_hit(is_long, price, trade.tp2):
            frac = min(TP2_FRACTION, trade.size_remaining)
            fill = self._apply_fill(trade, level=EXIT_TP2, price=trade.tp2, fraction=frac, now=now)
            trade.tp2_hit = True
            events.append({"type": "fill", "level": EXIT_TP2, "trade_id": trade.trade_id, "fill": fill})
            if trade.size_remaining <= 1e-9:
                self._finalize_if_flat(trade, exit_reason=EXIT_TP2, exit_price=trade.tp2, now=now)
                return events

        if trade.tp2_hit and not trade.tp3_hit and tp_hit(is_long, price, trade.tp3):
            frac = trade.size_remaining  # close remainder at TP3
            fill = self._apply_fill(trade, level=EXIT_TP3, price=trade.tp3, fraction=frac, now=now)
            trade.tp3_hit = True
            events.append({"type": "fill", "level": EXIT_TP3, "trade_id": trade.trade_id, "fill": fill})
            self._finalize_if_flat(trade, exit_reason=EXIT_TP3, exit_price=trade.tp3, now=now)

        return events

    def _apply_fill(
        self,
        trade: PaperTrade,
        *,
        level: str,
        price: float,
        fraction: float,
        now: int,
    ) -> Fill:
        is_long = trade.direction == "LONG"
        risk_usd = trade.risk_usd()
        r_part = price_to_r(trade.entry, price, trade.stop_loss, is_long=is_long) * fraction
        pnl_part = risk_usd * r_part
        fill = Fill(
            level=level,
            price=float(price),
            fraction=float(fraction),
            pnl_usd=round(pnl_part, 6),
            r_multiple=round(r_part, 6),
            ts=now,
        )
        trade.fills.append(fill)
        trade.size_remaining = max(0.0, round(trade.size_remaining - fraction, 8))
        trade.pnl_usd = round(sum(f.pnl_usd for f in trade.fills), 6)
        trade.r_multiple = round(sum(f.r_multiple for f in trade.fills), 6)
        return fill

    def _finalize_if_flat(
        self,
        trade: PaperTrade,
        *,
        exit_reason: str,
        exit_price: float,
        now: int,
    ) -> None:
        if trade.size_remaining > 1e-9:
            return
        trade.status = STATUS_CLOSED
        trade.closed_at = now
        trade.holding_seconds = max(0, now - trade.opened_at)
        trade.exit_reason = exit_reason
        trade.exit_price = float(exit_price)
        self.equity = round(self.equity + trade.pnl_usd, 6)
        self._emit({
            "type": "closed",
            "trade_id": trade.trade_id,
            "exit_reason": exit_reason,
            "pnl_usd": trade.pnl_usd,
            "r_multiple": trade.r_multiple,
        })

    def force_close(self, trade_id: str, price: float, *, ts: int | None = None) -> PaperTrade:
        now = int(ts if ts is not None else time.time())
        trade = self.trades[trade_id]
        if trade.status != STATUS_OPEN:
            return trade
        if trade.size_remaining > 1e-9:
            self._apply_fill(
                trade,
                level=EXIT_MANUAL,
                price=float(price),
                fraction=trade.size_remaining,
                now=now,
            )
        self._finalize_if_flat(trade, exit_reason=EXIT_MANUAL, exit_price=float(price), now=now)
        return trade

    def snapshot(self) -> dict[str, Any]:
        return {
            "equity": self.equity,
            "initial_equity": self.initial_equity,
            "pending_signals": [s.to_dict() for s in self.pending_signals()],
            "open_trades": [t.to_dict() for t in self.open_trades()],
            "closed_trades": [t.to_dict() for t in self.closed_trades()],
            "stats": self.stats(),
            "stats_by_strategy": self.stats_by_strategy(),
        }
