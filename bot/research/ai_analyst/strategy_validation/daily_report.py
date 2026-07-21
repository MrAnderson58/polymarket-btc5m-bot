"""S48 — daily strategy validation report."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bot.research.ai_analyst.strategy_validation.dashboard import build_dashboard
from bot.research.ai_analyst.strategy_validation.ranking import build_ranking_report


def day_start_ts(ts: int | None = None) -> int:
    ts = int(ts or datetime.now().timestamp())
    dt = datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(dt.timestamp())


def format_daily_report(
    outcomes_today: list[dict[str, Any]],
    *,
    signals_today: int = 0,
    open_count: int = 0,
) -> str:
    dash = build_dashboard(outcomes_today, open_count=open_count)
    ranking = build_ranking_report(outcomes_today)
    best = ranking.get("best_setup") or {}
    worst = ranking.get("worst_setup") or {}
    pf = dash.get("profit_factor")
    pf_s = "inf" if pf is None and dash.get("profit_factor_raw") == float("inf") else (
        f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)
    )
    return "\n".join([
        "Signals today",
        "",
        f"Trades:\n{dash.get('trades', 0)}",
        "",
        f"Win:\n{dash.get('wins', 0)}",
        "",
        f"Loss:\n{dash.get('losses', 0)}",
        "",
        f"WinRate:\n{dash.get('win_rate', 0)}%",
        "",
        f"PF:\n{pf_s}",
        "",
        f"Expectancy:\n{dash.get('expectancy', 0)}R",
        "",
        f"Best setup:\n{best.get('name') or 'n/a'}",
        "",
        f"Worst setup:\n{worst.get('name') or 'n/a'}",
        "",
        f"(Signals created today: {signals_today})",
    ])
