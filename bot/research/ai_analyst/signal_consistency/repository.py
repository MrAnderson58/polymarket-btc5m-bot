"""S51/S53 — Single Source of Truth for AI signals + Paper Trading books.

Canonical books:
  Paper Trading (production) → market_events_paper_trades_s42
  AI paper opens             → ai_paper_trades_s47
  AI signals / outcomes      → ai_signal_history_s48 / ai_signal_outcomes_s48

doctor Open Trades + Telegram /open /stats → S42 paper book
Telegram /signals + doctor Signals today  → S48
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, ContextManager

from bot.research.ai_analyst.strategy_validation.dashboard import build_dashboard
from bot.research.ai_analyst.strategy_validation.store import (
    load_outcomes,
    load_signal_history,
)


def day_start_local_ts(*, now: float | None = None) -> int:
    """Local midnight — shared by doctor and Telegram day windows."""
    dt = datetime.fromtimestamp(now if now is not None else time.time())
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


@dataclass(frozen=True)
class SignalSnapshot:
    """Consistent snapshot across doctor / telegram surfaces."""

    open_count: int  # Paper Trading S42 opens
    ai_open_count: int  # S47 AI paper opens
    signals_today: int  # S48
    signals_total_sample: int
    outcomes_count: int
    day_start_ts: int
    open_trades: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)  # S42 paper dashboard
    book: str = "paper_s42+ai_s48"

    def consistency_tuple(self) -> tuple[int, int, int]:
        """(paper_open, signals_today, paper_closed) for equality checks."""
        return (
            int(self.open_count),
            int(self.signals_today),
            int(self.stats.get("closed_trades") or self.outcomes_count),
        )


class SignalTruthRepository:
    """One repository used by /signals /open /stats /report doctor paper UX."""

    def __init__(
        self,
        conn_factory: Callable[[], ContextManager[Any]] | None = None,
    ) -> None:
        self._conn_factory = conn_factory

    def _factory(self) -> ContextManager[Any]:
        if self._conn_factory is not None:
            return self._conn_factory()
        from bot.research.market_events.db import market_events_readonly_connection
        return market_events_readonly_connection()

    # ── Paper Trading book (S42) ─────────────────────────────────────────

    def count_paper_open_trades(self, conn: Any | None = None) -> int:
        def _q(c: Any) -> int:
            try:
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42 "
                    "WHERE status = 'OPEN'",
                ).fetchone()
                return int((row["n"] if row else 0) or 0)
            except Exception:
                return 0

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return 0

    def list_paper_open_trades(self, *, limit: int = 20, conn: Any | None = None) -> list[dict[str, Any]]:
        def _q(c: Any) -> list[dict[str, Any]]:
            from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
                list_open_trades_s42,
            )
            return list_open_trades_s42(c, limit=limit)

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return []

    def paper_stats_dashboard(self, conn: Any | None = None) -> dict[str, Any]:
        def _q(c: Any) -> dict[str, Any]:
            from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
                paper_performance_dashboard_s42,
            )
            return paper_performance_dashboard_s42(c)

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return {
                "open_trades": 0,
                "closed_trades": 0,
                "winrate_pct": 0.0,
                "today_pnl_usd": 0.0,
                "current_equity": 0.0,
            }

    # Alias used by doctor /open after S53 — Paper Trading opens
    def count_open_trades(self, conn: Any | None = None) -> int:
        return self.count_paper_open_trades(conn)

    def list_open_trades(self, *, limit: int = 20, conn: Any | None = None) -> list[dict[str, Any]]:
        return self.list_paper_open_trades(limit=limit, conn=conn)

    def stats_dashboard(self, *, outcome_limit: int = 500, conn: Any | None = None) -> dict[str, Any]:
        """Paper Trading stats (S42). outcome_limit kept for API compat."""
        _ = outcome_limit
        return self.paper_stats_dashboard(conn)

    # ── AI paper book (S47) ──────────────────────────────────────────────

    def count_ai_open_trades(self, conn: Any | None = None) -> int:
        def _q(c: Any) -> int:
            try:
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM ai_paper_trades_s47 WHERE status = 'OPEN'",
                ).fetchone()
                return int((row["n"] if row else 0) or 0)
            except Exception:
                return 0

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return 0

    def list_ai_open_trades(self, *, limit: int = 20, conn: Any | None = None) -> list[dict[str, Any]]:
        def _q(c: Any) -> list[dict[str, Any]]:
            try:
                rows = c.execute(
                    """
                    SELECT * FROM ai_paper_trades_s47
                    WHERE status = 'OPEN'
                    ORDER BY opened_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return []

    # ── AI signals (S48) ─────────────────────────────────────────────────

    def count_signals_today(self, *, now: float | None = None, conn: Any | None = None) -> int:
        start = day_start_local_ts(now=now)

        def _q(c: Any) -> int:
            try:
                row = c.execute(
                    """
                    SELECT COUNT(*) AS n FROM ai_signal_history_s48
                    WHERE created_at >= ?
                    """,
                    (start,),
                ).fetchone()
                return int((row["n"] if row else 0) or 0)
            except Exception:
                return 0

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return 0

    def list_signals(self, *, limit: int = 50, conn: Any | None = None) -> list[dict[str, Any]]:
        def _q(c: Any) -> list[dict[str, Any]]:
            try:
                return load_signal_history(c, limit=limit)
            except Exception:
                return []

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return []

    def latest_signal(self, conn: Any | None = None) -> dict[str, Any] | None:
        rows = self.list_signals(limit=1, conn=conn)
        return rows[0] if rows else None

    def list_outcomes(
        self,
        *,
        limit: int = 500,
        since_ts: int | None = None,
        conn: Any | None = None,
    ) -> list[dict[str, Any]]:
        def _q(c: Any) -> list[dict[str, Any]]:
            try:
                return load_outcomes(c, limit=limit, since_ts=since_ts)
            except Exception:
                return []

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return []

    def ai_validation_dashboard(self, *, outcome_limit: int = 500, conn: Any | None = None) -> dict[str, Any]:
        """S48 strategy validation metrics (separate from Paper Trading S42)."""
        def _q(c: Any) -> dict[str, Any]:
            open_n = self.count_ai_open_trades(c)
            outcomes = self.list_outcomes(limit=outcome_limit, conn=c)
            return build_dashboard(outcomes, open_count=open_n)

        if conn is not None:
            return _q(conn)
        try:
            with self._factory() as c:
                return _q(c)
        except Exception:
            return build_dashboard([], open_count=0)

    def snapshot(
        self,
        *,
        signal_limit: int = 50,
        open_limit: int = 50,
        outcome_limit: int = 500,
        now: float | None = None,
    ) -> SignalSnapshot:
        """One atomic read for consistency across doctor / telegram / tests."""
        start = day_start_local_ts(now=now)
        _ = outcome_limit
        try:
            with self._factory() as c:
                open_trades = self.list_paper_open_trades(limit=open_limit, conn=c)
                open_count = self.count_paper_open_trades(c)
                ai_open = self.count_ai_open_trades(c)
                signals_today = self.count_signals_today(now=now, conn=c)
                signals = self.list_signals(limit=signal_limit, conn=c)
                stats = self.paper_stats_dashboard(c)
                return SignalSnapshot(
                    open_count=open_count,
                    ai_open_count=ai_open,
                    signals_today=signals_today,
                    signals_total_sample=len(signals),
                    outcomes_count=int(stats.get("closed_trades") or 0),
                    day_start_ts=start,
                    open_trades=open_trades,
                    signals=signals,
                    outcomes=[],
                    stats=stats,
                )
        except Exception:
            return SignalSnapshot(
                open_count=0,
                ai_open_count=0,
                signals_today=0,
                signals_total_sample=0,
                outcomes_count=0,
                day_start_ts=start,
                stats={"open_trades": 0, "closed_trades": 0},
            )


_REPO: SignalTruthRepository | None = None


def get_repository() -> SignalTruthRepository:
    global _REPO
    if _REPO is None:
        _REPO = SignalTruthRepository()
    return _REPO


# S53 — command → table source map (for db-info / docs)
COMMAND_DATA_SOURCES: dict[str, str] = {
    "paper-performance": "market_events_paper_trades_s42 (+ account/reports)",
    "trading-audit": "market_events_paper_trades_s42",
    "doctor.open_trades": "market_events_paper_trades_s42",
    "doctor.signals_today": "ai_signal_history_s48",
    "/open": "market_events_paper_trades_s42",
    "/stats": "market_events_paper_trades_s42 + market_events_paper_account_s42",
    "/signals": "ai_signal_history_s48",
    "/report": "ai_signal_history_s48 + context (AI signal card)",
    "ai_paper (S47)": "ai_paper_trades_s47 (separate AI multi-target book)",
}
