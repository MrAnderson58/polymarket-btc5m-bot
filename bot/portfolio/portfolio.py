"""Portfolio state — balance, equity, streaks, drawdown."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass

from bot.config import PORTFOLIO_STARTING_BALANCE_USDC
from bot.database import daily_realized_pnl_usdc


@dataclass
class PortfolioState:
    balance: float = 0.0
    equity: float = 0.0
    peak_equity: float = 0.0
    daily_pnl: float = 0.0
    drawdown: float = 0.0
    win_streak: int = 0
    loss_streak: int = 0
    current_risk: str = "LOW"
    paused: bool = False
    pause_reason: str | None = None
    pause_until_ts: int | None = None
    guard_reset_after_ts: int | None = None


class PortfolioManager:
    def __init__(self, state: PortfolioState | None = None) -> None:
        self.state = state or PortfolioState()

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> PortfolioManager:
        row = conn.execute(
            "SELECT payload_json FROM portfolio_state WHERE id = 1"
        ).fetchone()
        if not row:
            return cls(
                PortfolioState(
                    balance=PORTFOLIO_STARTING_BALANCE_USDC,
                    equity=PORTFOLIO_STARTING_BALANCE_USDC,
                    peak_equity=PORTFOLIO_STARTING_BALANCE_USDC,
                )
            )
        data = json.loads(row["payload_json"])
        return cls(PortfolioState(**data))

    def save(self, conn: sqlite3.Connection) -> None:
        payload = json.dumps(asdict(self.state), ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO portfolio_state (id, payload_json, updated_at)
            VALUES (1, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (payload,),
        )

    def refresh(self, conn: sqlite3.Connection) -> None:
        daily = daily_realized_pnl_usdc(conn)
        self.state.daily_pnl = daily
        self.state.equity = self.state.balance + daily
        self.state.peak_equity = max(self.state.peak_equity, self.state.equity)
        if self.state.peak_equity > 0:
            self.state.drawdown = (
                (self.state.peak_equity - self.state.equity) / self.state.peak_equity * 100
            )
        else:
            self.state.drawdown = 0.0

        rows = conn.execute(
            """
            SELECT pnl_usdc
            FROM early_reversion_v2_trades
            WHERE status = 'closed'
            ORDER BY closed_at DESC, entry_ts DESC
            LIMIT 20
            """
        ).fetchall()
        win_streak = 0
        loss_streak = 0
        for row in rows:
            pnl = float(row["pnl_usdc"] or 0)
            if pnl > 0:
                if loss_streak:
                    break
                win_streak += 1
            elif pnl < 0:
                if win_streak:
                    break
                loss_streak += 1
            else:
                break
        self.state.win_streak = win_streak
        self.state.loss_streak = loss_streak
        self.state.current_risk = self._risk_label()
        now_ts = int(time.time())
        self.state.paused = bool(
            self.state.pause_until_ts and self.state.pause_until_ts > now_ts
        )

    def pause(
        self,
        conn: sqlite3.Connection,
        *,
        reason: str,
        detail: str = "",
        pause_sec: int | None = None,
    ) -> None:
        self.state.paused = True
        self.state.pause_reason = reason if not detail else f"{reason} ({detail})"
        if pause_sec is not None:
            self.state.pause_until_ts = int(time.time()) + pause_sec
        else:
            self.state.pause_until_ts = None
        self.save(conn)

    def clear_pause(self, conn: sqlite3.Connection) -> None:
        self.state.paused = False
        self.state.pause_reason = None
        self.state.pause_until_ts = None
        self.state.guard_reset_after_ts = int(time.time())
        self.save(conn)

    def _risk_label(self) -> str:
        if self.state.drawdown >= 15 or self.state.loss_streak >= 3:
            return "HIGH"
        if self.state.drawdown >= 5 or self.state.loss_streak >= 2:
            return "MEDIUM"
        return "LOW"
