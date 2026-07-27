"""Shock paper performance analytics — read-only; no trading logic changes."""

from __future__ import annotations

import csv
import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bot.research.market_events.config import MARKET_EVENTS_DATABASE_PATH
from bot.research.market_events.db_config import resolve_market_events_db_config
from bot.research.market_events.paper_execution import net_return

DEFAULT_NOTIONAL_USD = float(os.getenv("ME_PAPER_NOTIONAL_USD", "100"))
DEFAULT_STARTING_EQUITY = float(os.getenv("ME_PAPER_STARTING_EQUITY", "1000"))

COMPLETED_RUNS_SQL = """
SELECT
  r.id,
  r.event_id,
  r.strategy_name,
  r.reversal_variant,
  r.exit_variant,
  r.entry_ts,
  r.exit_ts,
  r.entry_price,
  r.exit_price,
  r.exit_reason,
  r.gross_return,
  r.net_return,
  r.fee_bps,
  r.slippage_bps,
  r.duration_seconds,
  r.mfe,
  r.mae,
  e.symbol,
  e.direction AS shock_direction
FROM paper_strategy_runs r
JOIN market_events e ON e.id = r.event_id
WHERE r.exit_ts IS NOT NULL
  AND r.entry_ts IS NOT NULL
  AND r.eligibility = 1
ORDER BY r.exit_ts ASC, r.id ASC
"""


@dataclass
class CompletedTrade:
    run_id: int
    event_id: int
    strategy_name: str
    reversal_variant: str
    exit_variant: str
    symbol: str
    shock_direction: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    exit_reason: str | None
    gross_return_pct: float
    net_return_pct: float
    duration_sec: int
    pnl_usd: float
    side: str

    @property
    def timestamp(self) -> int:
        return self.exit_ts


@dataclass
class PerformanceStats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    gross_profit_usd: float = 0.0
    gross_loss_usd: float = 0.0
    net_profit_usd: float = 0.0
    avg_winner_usd: float = 0.0
    avg_loser_usd: float = 0.0
    profit_factor: float | None = None
    expectancy_usd: float = 0.0
    avg_duration_sec: float = 0.0
    largest_winner_usd: float = 0.0
    largest_loser_usd: float = 0.0
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    max_drawdown_usd: float = 0.0
    starting_equity_usd: float = DEFAULT_STARTING_EQUITY
    ending_equity_usd: float = DEFAULT_STARTING_EQUITY
    current_equity_usd: float = DEFAULT_STARTING_EQUITY
    date_range_start: int | None = None
    date_range_end: int | None = None
    equity_points: list[tuple[int, float]] = field(default_factory=list)
    by_strategy: dict[str, "PerformanceStats"] = field(default_factory=dict)


def _trade_side(shock_direction: str, reversal_variant: str) -> str:
    """Paper trades fade the shock (opposite direction)."""
    d = (shock_direction or "").upper()
    if d == "UP":
        return "SHORT"
    if d == "DOWN":
        return "LONG"
    return f"REV_{reversal_variant}"


def _row_to_trade(row: Any, *, notional: float) -> CompletedTrade | None:
    net_pct = row["net_return"]
    gross_pct = row["gross_return"]
    if net_pct is None and gross_pct is not None:
        fee = float(row["fee_bps"] or 10.0)
        slip = float(row["slippage_bps"] or 10.0)
        net_pct = net_return(float(gross_pct), fee_bps=fee, slippage_bps=slip)
    if net_pct is None:
        return None
    net_f = float(net_pct)
    pnl = notional * net_f / 100.0
    entry_ts = int(row["entry_ts"])
    exit_ts = int(row["exit_ts"])
    dur = row["duration_seconds"]
    if dur is None:
        dur = max(0, exit_ts - entry_ts)
    return CompletedTrade(
        run_id=int(row["id"]),
        event_id=int(row["event_id"]),
        strategy_name=str(row["strategy_name"]),
        reversal_variant=str(row["reversal_variant"]),
        exit_variant=str(row["exit_variant"]),
        symbol=str(row["symbol"]),
        shock_direction=str(row["shock_direction"] or ""),
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=float(row["entry_price"] or 0),
        exit_price=float(row["exit_price"] or 0),
        exit_reason=row["exit_reason"],
        gross_return_pct=float(gross_pct or net_f),
        net_return_pct=net_f,
        duration_sec=int(dur),
        pnl_usd=pnl,
        side=_trade_side(str(row["shock_direction"] or ""), str(row["reversal_variant"])),
    )


def load_completed_trades(
    conn: Any,
    *,
    notional: float = DEFAULT_NOTIONAL_USD,
    since_ts: int | None = None,
) -> list[CompletedTrade]:
    sql = COMPLETED_RUNS_SQL
    params: tuple[Any, ...] = ()
    if since_ts is not None:
        sql = sql.replace(
            "WHERE r.exit_ts IS NOT NULL",
            "WHERE r.exit_ts IS NOT NULL AND r.exit_ts >= ?",
        )
        params = (since_ts,)
    rows = conn.execute(sql, params).fetchall()
    out: list[CompletedTrade] = []
    for row in rows:
        t = _row_to_trade(row, notional=notional)
        if t:
            out.append(t)
    return out


def _streaks(pnls: list[float]) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
        elif p < 0:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        best_w = max(best_w, cur_w)
        best_l = max(best_l, cur_l)
    return best_w, best_l


def compute_performance(
    trades: list[CompletedTrade],
    *,
    starting_equity: float = DEFAULT_STARTING_EQUITY,
    include_strategy_breakdown: bool = True,
) -> PerformanceStats:
    stats = PerformanceStats(starting_equity_usd=starting_equity)
    if not trades:
        stats.ending_equity_usd = starting_equity
        stats.current_equity_usd = starting_equity
        return stats

    stats.trades = len(trades)
    stats.date_range_start = trades[0].exit_ts
    stats.date_range_end = trades[-1].exit_ts

    pnls = [t.pnl_usd for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    stats.wins = len(wins)
    stats.losses = len(losses)
    stats.breakeven = stats.trades - stats.wins - stats.losses
    stats.win_rate = stats.wins / stats.trades if stats.trades else 0.0

    stats.gross_profit_usd = sum(wins)
    stats.gross_loss_usd = sum(losses)
    stats.net_profit_usd = sum(pnls)
    stats.avg_winner_usd = statistics.mean(wins) if wins else 0.0
    stats.avg_loser_usd = statistics.mean(losses) if losses else 0.0
    gl = abs(stats.gross_loss_usd)
    stats.profit_factor = (stats.gross_profit_usd / gl) if gl > 1e-12 else None
    stats.expectancy_usd = statistics.mean(pnls) if pnls else 0.0
    durs = [float(t.duration_sec) for t in trades]
    stats.avg_duration_sec = statistics.mean(durs) if durs else 0.0
    stats.largest_winner_usd = max(pnls) if pnls else 0.0
    stats.largest_loser_usd = min(pnls) if pnls else 0.0
    stats.longest_win_streak, stats.longest_loss_streak = _streaks(pnls)

    equity = starting_equity
    peak = equity
    max_dd = 0.0
    stats.equity_points = [(trades[0].entry_ts, equity)]
    for t in trades:
        equity += t.pnl_usd
        stats.equity_points.append((t.exit_ts, equity))
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    stats.max_drawdown_usd = max_dd
    stats.ending_equity_usd = equity
    stats.current_equity_usd = equity

    if include_strategy_breakdown:
        by_name: dict[str, list[CompletedTrade]] = {}
        for t in trades:
            by_name.setdefault(t.strategy_name, []).append(t)
        for name, subset in sorted(by_name.items()):
            stats.by_strategy[name] = compute_performance(
                subset,
                starting_equity=starting_equity,
                include_strategy_breakdown=False,
            )

    return stats


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def format_performance_report(
    stats: PerformanceStats,
    *,
    db_path: Path | None = None,
    notional: float = DEFAULT_NOTIONAL_USD,
) -> str:
    cfg = resolve_market_events_db_config()
    path = db_path or cfg.sqlite_path or MARKET_EVENTS_DATABASE_PATH
    lines = [
        "PERFORMANCE REPORT",
        "",
        "Database",
        str(path),
        "",
        f"Trades ........... {stats.trades}",
        f"Notional/trade ... {_fmt_usd(notional)}",
        f"Date Range ....... {_fmt_ts(stats.date_range_start)} → {_fmt_ts(stats.date_range_end)}",
        "",
        "------------------------",
        "",
        f"Trades ........... {stats.trades}",
        f"Wins ............. {stats.wins}",
        f"Losses ........... {stats.losses}",
        f"Win Rate ......... {_fmt_pct(stats.win_rate)}",
        f"Gross Profit ..... {_fmt_usd(stats.gross_profit_usd)}",
        f"Gross Loss ....... {_fmt_usd(stats.gross_loss_usd)}",
        f"Net Profit ....... {_fmt_usd(stats.net_profit_usd)}",
        f"Profit Factor .... {stats.profit_factor:.2f}" if stats.profit_factor is not None else "Profit Factor .... —",
        f"Expectancy ....... {_fmt_usd(stats.expectancy_usd)}",
        f"Average Win ...... {_fmt_usd(stats.avg_winner_usd)}",
        f"Average Loss ..... {_fmt_usd(stats.avg_loser_usd)}",
        f"Largest Win ...... {_fmt_usd(stats.largest_winner_usd)}",
        f"Largest Loss ..... {_fmt_usd(stats.largest_loser_usd)}",
        f"Average Duration . {stats.avg_duration_sec:.0f}s",
        f"Max Drawdown ..... {_fmt_usd(stats.max_drawdown_usd)}",
        f"Win Streak ....... {stats.longest_win_streak}",
        f"Loss Streak ...... {stats.longest_loss_streak}",
        f"Current Equity ... {_fmt_usd(stats.current_equity_usd)}",
        f"Starting Equity .. {_fmt_usd(stats.starting_equity_usd)}",
        f"Ending Equity .... {_fmt_usd(stats.ending_equity_usd)}",
    ]

    if stats.by_strategy:
        lines.extend(["", "STRATEGY BREAKDOWN", ""])
        lines.append(
            f"{'Strategy':<28} {'Trades':>6} {'Win%':>7} {'Net PnL':>10} {'PF':>6} {'Exp':>8} {'MaxDD':>8}",
        )
        for name, st in sorted(stats.by_strategy.items()):
            pf = f"{st.profit_factor:.2f}" if st.profit_factor is not None else "—"
            lines.append(
                f"{name[:28]:<28} {st.trades:>6} {_fmt_pct(st.win_rate):>7} "
                f"{_fmt_usd(st.net_profit_usd):>10} {pf:>6} {_fmt_usd(st.expectancy_usd):>8} "
                f"{_fmt_usd(st.max_drawdown_usd):>8}",
            )
    return "\n".join(lines)


def format_equity_curve_ascii(
    stats: PerformanceStats,
    *,
    width: int = 28,
    height: int = 12,
) -> str:
    points = stats.equity_points
    if len(points) < 2:
        return "Equity\n\n(no closed trades — equity curve empty)\n"

    values = [p[1] for p in points]
    lo = min(min(values), stats.starting_equity_usd)
    hi = max(max(values), stats.starting_equity_usd)
    if hi - lo < 1e-9:
        hi = lo + 1.0

    def y_for(v: float) -> int:
        return int(round((v - lo) / (hi - lo) * (height - 1)))

    sampled: list[float] = []
    if len(values) <= width:
        sampled = values
    else:
        step = (len(values) - 1) / (width - 1)
        for i in range(width):
            idx = int(round(i * step))
            sampled.append(values[min(idx, len(values) - 1)])

    grid = [[" "] * len(sampled) for _ in range(height)]
    for col, v in enumerate(sampled):
        row = height - 1 - y_for(v)
        grid[row][col] = "●"

    start_row = height - 1 - y_for(stats.starting_equity_usd)
    for col in range(len(sampled)):
        if grid[start_row][col] == " ":
            grid[start_row][col] = "─"

    lines = ["Equity", ""]
    for r in range(height):
        y_val = hi - (hi - lo) * r / (height - 1)
        label = f"{y_val:>7.0f} ┤"
        lines.append(label + "".join(grid[r]))
    lines.append("        └" + "─" * len(sampled))
    lines.append(f"        start {_fmt_usd(stats.starting_equity_usd)}  end {_fmt_usd(stats.ending_equity_usd)}")
    return "\n".join(lines)


def export_performance_csv(
    trades: list[CompletedTrade],
    path: Path | str,
    *,
    starting_equity: float = DEFAULT_STARTING_EQUITY,
) -> int:
    path = Path(path)
    equity = starting_equity
    fieldnames = [
        "timestamp",
        "strategy",
        "side",
        "symbol",
        "entry",
        "exit",
        "pnl",
        "pnl_pct",
        "duration",
        "cumulative_equity",
        "exit_reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for t in trades:
            equity += t.pnl_usd
            w.writerow({
                "timestamp": t.exit_ts,
                "strategy": t.strategy_name,
                "side": t.side,
                "symbol": t.symbol,
                "entry": t.entry_price,
                "exit": t.exit_price,
                "pnl": round(t.pnl_usd, 4),
                "pnl_pct": round(t.net_return_pct, 4),
                "duration": t.duration_sec,
                "cumulative_equity": round(equity, 4),
                "exit_reason": t.exit_reason or "",
            })
    return len(trades)


def run_performance_cli(
    conn: Any,
    *,
    equity: bool = False,
    csv_path: Path | str | None = None,
    days: int | None = None,
    notional: float = DEFAULT_NOTIONAL_USD,
    starting_equity: float = DEFAULT_STARTING_EQUITY,
) -> str:
    since = None
    if days is not None and days > 0:
        since = int(datetime.now(tz=timezone.utc).timestamp()) - days * 86400
    trades = load_completed_trades(conn, notional=notional, since_ts=since)
    stats = compute_performance(trades, starting_equity=starting_equity)

    parts: list[str] = []
    parts.append(format_performance_report(stats, notional=notional))
    if equity:
        parts.append(format_equity_curve_ascii(stats))
    if csv_path is not None:
        n = export_performance_csv(trades, csv_path, starting_equity=starting_equity)
        parts.append(f"Exported {n} trades → {csv_path}")
    return "\n\n".join(parts)
