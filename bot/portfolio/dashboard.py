"""Live dashboard data assembly."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.config import LIVE_MODE, TRADING_MODE, is_live_trading_enabled
from bot.database import count_all_open_positions, daily_realized_pnl_usdc
from bot.portfolio.approval import evaluate_live_approval
from bot.portfolio.guards import check_portfolio_guards
from bot.portfolio.kill_switch import is_kill_switch_active, kill_switch_reason
from bot.portfolio.portfolio import PortfolioManager
from bot.portfolio.sizing import effective_position_size_usdc
from bot.scientist.builder import build_scientist_section
from bot.trading_brain.report import build_brain_report


def _winrate(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN pnl_usdc > 0 THEN 1 ELSE 0 END) AS wins,
            COUNT(*) AS total
        FROM early_reversion_v2_trades
        WHERE status = 'closed'
        """
    ).fetchone()
    total = int(row["total"] or 0)
    if total == 0:
        return 0.0
    return int(row["wins"] or 0) / total


def build_dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    manager = PortfolioManager.load(conn)
    manager.refresh(conn)
    guard = check_portfolio_guards(conn)
    approval = evaluate_live_approval(conn)
    brain = build_brain_report(conn)
    scientist = build_scientist_section(conn, run_cycle=False)

    open_trades = conn.execute(
        """
        SELECT id, market_slug, strategy_name, side, entry_price, entry_ts
        FROM early_reversion_v2_trades
        WHERE status = 'open'
        ORDER BY entry_ts DESC
        """
    ).fetchall()

    paused = guard.paused or is_kill_switch_active()
    pause_reason = guard.reason or kill_switch_reason()

    return {
        "trading_mode": TRADING_MODE,
        "live_mode": LIVE_MODE,
        "position_size_usdc": effective_position_size_usdc(),
        "balance": round(manager.state.balance, 2),
        "equity": round(manager.state.equity, 2),
        "pnl_today_usdc": round(daily_realized_pnl_usdc(conn), 2),
        "drawdown_pct": round(manager.state.drawdown, 2),
        "winrate": round(_winrate(conn), 3),
        "open_trades": len(open_trades),
        "open_positions": [dict(row) for row in open_trades],
        "risk": manager.state.current_risk,
        "win_streak": manager.state.win_streak,
        "loss_streak": manager.state.loss_streak,
        "paused": paused,
        "pause_reason": pause_reason,
        "kill_switch": is_kill_switch_active(),
        "live_trading": is_live_trading_enabled(),
        "latest_approval": approval.summary,
        "brain_mode": brain.get("mode"),
        "scientist_mode": scientist.get("mode"),
        "ai_decision": approval.ai_decision,
    }


def format_dashboard(data: dict[str, Any]) -> str:
    lines = [
        "=== Live Dashboard ===",
        f"Mode: {data['trading_mode']} / {data['live_mode']} @ ${data['position_size_usdc']:.2f}",
        f"Balance: ${data['balance']:.2f}  Equity: ${data['equity']:.2f}",
        f"PnL today: ${data['pnl_today_usdc']:+.2f}",
        f"Open trades: {data['open_trades']}  Winrate: {data['winrate']:.1%}",
        f"Risk: {data['risk']}  Drawdown: {data['drawdown_pct']:.1f}%",
        f"Streaks: W{data['win_streak']} / L{data['loss_streak']}",
    ]
    if data["paused"]:
        lines.append(f"PAUSED: {data['pause_reason']}")
    if data["kill_switch"]:
        lines.append("KILL SWITCH: active")
    lines.extend(
        [
            "",
            "Latest approval snapshot:",
            data["latest_approval"],
            "",
            f"Brain: {data['brain_mode']}  Scientist: {data['scientist_mode']}  AI: {data['ai_decision']}",
        ]
    )
    return "\n".join(lines)
