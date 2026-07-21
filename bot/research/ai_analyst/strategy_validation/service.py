"""S48 — orchestration: history + outcomes + dashboard + ranking."""

from __future__ import annotations

import time
from typing import Any

from bot.research.ai_analyst.paper_trading.engine import PaperTradingEngine
from bot.research.ai_analyst.paper_trading.models import PaperTrade, STATUS_CLOSED
from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.strategy_validation.daily_report import (
    day_start_ts,
    format_daily_report,
)
from bot.research.ai_analyst.strategy_validation.dashboard import (
    build_dashboard,
    format_dashboard,
)
from bot.research.ai_analyst.strategy_validation.history import (
    SignalHistoryRecord,
    history_from_trading_signal,
)
from bot.research.ai_analyst.strategy_validation.outcomes import outcome_from_trade
from bot.research.ai_analyst.strategy_validation.ranking import (
    build_ranking_report,
    format_leaderboard,
)
from bot.research.ai_analyst.strategy_validation.store import (
    load_outcomes,
    load_signal_history,
    save_ranking,
    upsert_outcome,
    upsert_signal_history,
)


class StrategyValidationService:
    """In-memory + optional SQLite persistence for S48."""

    def __init__(self, *, conn: Any | None = None, engine: PaperTradingEngine | None = None) -> None:
        self.conn = conn
        self.engine = engine or PaperTradingEngine()
        self.history: dict[str, SignalHistoryRecord] = {}
        self.outcomes: dict[str, dict[str, Any]] = {}
        prev = self.engine._on_event

        def _hook(ev: dict[str, Any]) -> None:
            if prev:
                prev(ev)
            if ev.get("type") == "closed":
                trade_id = ev.get("trade_id")
                trade = self.engine.trades.get(str(trade_id or ""))
                if trade and trade.status == STATUS_CLOSED:
                    self.record_outcome(trade)

        self.engine._on_event = _hook

    def submit_signal(self, signal: TradingSignal, **history_kwargs: Any) -> SignalHistoryRecord:
        self.engine.submit_signal(signal)
        rec = history_from_trading_signal(signal, **history_kwargs)
        rec.status = signal.status
        self.history[rec.signal_id] = rec
        if self.conn is not None:
            upsert_signal_history(self.conn, rec.to_dict())
        return rec

    def record_outcome(self, trade: PaperTrade) -> dict[str, Any]:
        hist = self.history.get(trade.signal_id)
        row = outcome_from_trade(trade, history=hist)
        self.outcomes[trade.signal_id] = row
        if hist is not None:
            hist.status = "CLOSED"
            if self.conn is not None:
                upsert_signal_history(self.conn, hist.to_dict())
        if self.conn is not None:
            upsert_outcome(self.conn, row)
        return row

    def tick(self, symbol: str, price: float, *, ts: int | None = None) -> list[dict[str, Any]]:
        return self.engine.tick(symbol, price, ts=ts)

    def list_signals(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if self.conn is not None:
            return load_signal_history(self.conn, limit=limit)
        rows = sorted(self.history.values(), key=lambda r: r.created_at, reverse=True)
        return [r.to_dict() for r in rows[:limit]]

    def list_outcomes(self, *, limit: int = 200, since_ts: int | None = None) -> list[dict[str, Any]]:
        if self.conn is not None:
            return load_outcomes(self.conn, limit=limit, since_ts=since_ts)
        rows = list(self.outcomes.values())
        if since_ts is not None:
            rows = [r for r in rows if int(r.get("closed_at") or 0) >= since_ts]
        rows.sort(key=lambda r: int(r.get("closed_at") or 0), reverse=True)
        return rows[:limit]

    def dashboard(self) -> dict[str, Any]:
        outcomes = self.list_outcomes(limit=500)
        open_n = len(self.engine.open_trades())
        return build_dashboard(
            outcomes,
            open_count=open_n,
            initial_equity=self.engine.initial_equity,
        )

    def ranking(self) -> dict[str, Any]:
        outcomes = self.list_outcomes(limit=500)
        payload = build_ranking_report(outcomes)
        if self.conn is not None:
            from bot.research.ai_analyst.strategy_validation.ranking import format_ranking
            save_ranking(
                self.conn,
                ranking_type="full",
                body=format_ranking(payload),
                payload=payload,
                now=int(time.time()),
            )
        return payload

    def daily_report(self, *, now: int | None = None) -> str:
        now_ts = int(now or time.time())
        start = day_start_ts(now_ts)
        outcomes = self.list_outcomes(limit=200, since_ts=start)
        signals_today = sum(
            1 for s in self.list_signals(limit=200)
            if int(s.get("created_at") or 0) >= start
        )
        return format_daily_report(
            outcomes,
            signals_today=signals_today,
            open_count=len(self.engine.open_trades()),
        )

    def format_signals(self, *, limit: int = 15) -> str:
        rows = self.list_signals(limit=limit)
        lines = ["SIGNALS", f"count={len(rows)}", ""]
        if not rows:
            return "SIGNALS\n\n(none)"
        for r in rows:
            lines.extend([
                f"• {r['signal_id']}  {r['market']} {r['direction']}",
                f"  conf={r.get('confidence')} score={r.get('score')} [{r.get('strategy')}]",
                f"  entry={r.get('entry_low')}-{r.get('entry_high')} SL={r.get('stop_loss')}",
                f"  TP={r.get('tp1')}/{r.get('tp2')}/{r.get('tp3')} status={r.get('status')}",
                f"  {r.get('reasoning') or ''}",
                "",
            ])
        return "\n".join(lines).rstrip()

    def format_open(self) -> str:
        from bot.research.ai_analyst.paper_trading.reports import format_open_trades
        return format_open_trades(self.engine)

    def format_closed(self, *, limit: int = 20) -> str:
        rows = self.list_outcomes(limit=limit)
        lines = ["CLOSED OUTCOMES", f"showing={len(rows)}", ""]
        if not rows:
            return "CLOSED OUTCOMES\n\n(none)"
        for o in rows:
            lines.extend([
                f"• {o['signal_id']}  {o['market']} {o['direction']} [{o['strategy']}]",
                f"  {o['result']}  R={o['r_multiple']:.2f}  PnL=${o['pnl_usd']:.2f}",
                f"  hold={o['hold_time_sec']}s  MFE={o['mfe_pct']:.2f}% MAE={o['mae_pct']:.2f}%",
                f"  level={o.get('tp_level_reached')} exit={o.get('exit_reason')}",
                "",
            ])
        return "\n".join(lines).rstrip()

    def format_stats(self) -> str:
        return format_dashboard(self.dashboard())

    def format_leaderboard(self) -> str:
        return format_leaderboard(self.ranking())
