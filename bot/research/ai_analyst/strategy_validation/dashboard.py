"""S48 — strategy dashboard metrics."""

from __future__ import annotations

import math
from typing import Any

from bot.research.ai_analyst.paper_trading.models import EXIT_STOP, EXIT_TP1, EXIT_TP2, EXIT_TP3
from bot.research.ai_analyst.strategy_validation.outcomes import aggregate_exit_breakdown


def _max_drawdown_pct(pnls: list[float], *, initial: float = 10_000.0) -> float:
    if not pnls:
        return 0.0
    equity = float(initial)
    peak = float(initial)
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
        if equity <= 0:
            return 100.0
    return round(max_dd, 2)


def _sharpe(rs: list[float]) -> float | None:
    if len(rs) < 2:
        return None
    mean = sum(rs) / len(rs)
    var = sum((x - mean) ** 2 for x in rs) / (len(rs) - 1)
    if var <= 0:
        return None
    # Treat each trade as a period; annualize loosely with sqrt(252) if daily-ish
    return round(mean / math.sqrt(var) * math.sqrt(min(252, len(rs))), 4)


def build_dashboard(
    outcomes: list[dict[str, Any]],
    *,
    open_count: int = 0,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    if not outcomes:
        return {
            "trades": 0,
            "open_trades": open_count,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_r": 0.0,
            "avg_hold_seconds": 0.0,
            "tp1_pct": 0.0,
            "tp2_pct": 0.0,
            "tp3_pct": 0.0,
            "stop_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": None,
            "total_pnl_usd": 0.0,
        }

    # Chronological for drawdown
    ordered = sorted(outcomes, key=lambda o: int(o.get("closed_at") or 0))
    wins = [o for o in outcomes if float(o.get("pnl_usd") or 0) > 0]
    losses = [o for o in outcomes if float(o.get("pnl_usd") or 0) < 0]
    be = [o for o in outcomes if float(o.get("pnl_usd") or 0) == 0]
    gross_p = sum(float(o["pnl_usd"]) for o in wins)
    gross_l = abs(sum(float(o["pnl_usd"]) for o in losses))
    pf = (gross_p / gross_l) if gross_l > 0 else (float("inf") if gross_p > 0 else 0.0)
    rs = [float(o.get("r_multiple") or 0) for o in outcomes]
    avg_r = sum(rs) / len(rs)
    holds = [int(o.get("hold_time_sec") or 0) for o in outcomes]
    breakdown = aggregate_exit_breakdown(outcomes)
    pnls = [float(o.get("pnl_usd") or 0) for o in ordered]

    return {
        "trades": len(outcomes),
        "open_trades": open_count,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(be),
        "win_rate": round(100.0 * len(wins) / len(outcomes), 2),
        "profit_factor": round(pf, 4) if pf != float("inf") else None,
        "profit_factor_raw": pf,
        "expectancy": round(avg_r, 4),
        "avg_r": round(avg_r, 4),
        "avg_hold_seconds": round(sum(holds) / len(holds), 1),
        "tp1_pct": breakdown.get(EXIT_TP1, 0.0),
        "tp2_pct": breakdown.get(EXIT_TP2, 0.0),
        "tp3_pct": breakdown.get(EXIT_TP3, 0.0),
        "stop_pct": breakdown.get(EXIT_STOP, 0.0),
        "max_drawdown_pct": _max_drawdown_pct(pnls, initial=initial_equity),
        "sharpe": _sharpe(rs),
        "total_pnl_usd": round(sum(pnls), 4),
        "exit_breakdown": breakdown,
    }


def format_dashboard(dash: dict[str, Any]) -> str:
    trades = int(dash.get("trades") or 0)
    open_n = int(dash.get("open_trades") or 0)
    if trades <= 0:
        lines = [
            "STRATEGY DASHBOARD",
            "",
            f"Open trades: {open_n}",
            "",
            "Статистика появится после первой закрытой сделки.",
            "Stats will appear after the first closed trade.",
        ]
        return "\n".join(lines)

    pf = dash.get("profit_factor")
    pf_s = "inf" if pf is None and dash.get("profit_factor_raw") == float("inf") else (
        f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)
    )
    sharpe = dash.get("sharpe")
    sharpe_s = f"{sharpe:.2f}" if isinstance(sharpe, (int, float)) else "n/a"
    return "\n".join([
        "STRATEGY DASHBOARD",
        "",
        f"Trades: {trades}  Open: {open_n}",
        f"Win: {dash.get('wins', 0)}  Loss: {dash.get('losses', 0)}  BE: {dash.get('breakeven', 0)}",
        f"Win Rate: {dash.get('win_rate', 0)}%",
        f"Profit Factor: {pf_s}",
        f"Expectancy: {dash.get('expectancy', 0)}R",
        f"Average R: {dash.get('avg_r', 0)}",
        f"Average Hold Time: {dash.get('avg_hold_seconds', 0)}s",
        f"TP1 %: {dash.get('tp1_pct', 0)}%",
        f"TP2 %: {dash.get('tp2_pct', 0)}%",
        f"TP3 %: {dash.get('tp3_pct', 0)}%",
        f"Stop %: {dash.get('stop_pct', 0)}%",
        f"Max Drawdown: {dash.get('max_drawdown_pct', 0)}%",
        f"Sharpe: {sharpe_s}",
        f"Total PnL: ${dash.get('total_pnl_usd', 0)}",
    ])
