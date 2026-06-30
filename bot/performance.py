"""Performance analytics for Early Reversion V2, V2.5, V3, and V4 shadow trades."""

from __future__ import annotations

import sqlite3
import statistics
import sys
from dataclasses import dataclass, field

from bot.config import EARLY_REVERSION_POSITION_SIZE_USDC
from bot.database import connect, init_db

VERSION_TABLES: tuple[tuple[str, str, bool], ...] = (
    ("V2", "early_reversion_v2_trades", True),
    ("V2.5", "early_reversion_v25_trades", True),
    ("V3", "early_reversion_v3_trades", True),
    ("V4", "v4_shadow_trades", False),
)

STRATEGY_ORDER = ("NO_C", "YES_B", "YES_C", "YES_A", "NO_A", "NO_B")

PNL_BUCKETS: tuple[tuple[str, float | None, float | None], ...] = (
    ("<0%", None, 0.0),
    ("0-5%", 0.0, 5.0),
    ("5-10%", 5.0, 10.0),
    ("10-20%", 10.0, 20.0),
    ("20%+", 20.0, None),
)

EXIT_REASON_LABELS = {
    "TRAILING_STOP": "TRAILING_STOP",
    "TIME_STOP": "TIME_STOP",
    "STOP_LOSS": "STOP_LOSS",
    "RECOVERY_NO_POSITION": "RECOVERY",
}


@dataclass(frozen=True)
class NormalizedTrade:
    version: str
    strategy_name: str
    trade_id: int
    pnl_percent: float
    pnl_usdc: float
    exit_reason: str | None
    holding_time_seconds: float
    entry_price: float
    exit_price: float | None
    max_profit_pct: float | None
    realized_profit_pct: float | None
    profit_left_on_table_pct: float | None
    trailing_activation_price: float | None
    entry_score: float | None
    entry_probability: float | None
    closed_sort_key: str


@dataclass
class StrategyPerformance:
    version: str
    strategy_name: str
    trades: list[NormalizedTrade] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.version == "V4":
            return f"{self.version} {self.strategy_name}"
        return f"{self.version} {self.strategy_name}"

    @property
    def pnl_percents(self) -> list[float]:
        return [t.pnl_percent for t in self.trades]

    @property
    def wins(self) -> list[NormalizedTrade]:
        return [t for t in self.trades if t.pnl_percent > 0]

    @property
    def losses(self) -> list[NormalizedTrade]:
        return [t for t in self.trades if t.pnl_percent < 0]


@dataclass(frozen=True)
class VersionSummary:
    version: str
    trades: int
    win_rate: float
    average_pnl_percent: float
    profit_factor: float
    max_drawdown_percent: float


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _profit_factor(trades: list[NormalizedTrade]) -> float:
    gross_profit = sum(t.pnl_usdc for t in trades if t.pnl_usdc > 0)
    gross_loss = abs(sum(t.pnl_usdc for t in trades if t.pnl_usdc < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _pnl_percent_from_prices(entry_price: float, exit_price: float | None) -> float:
    if exit_price is None or entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100


def _pnl_usdc_from_prices(entry_price: float, exit_price: float | None) -> float:
    if exit_price is None or entry_price <= 0:
        return 0.0
    shares = EARLY_REVERSION_POSITION_SIZE_USDC / entry_price
    return shares * (exit_price - entry_price)


def _activation_profit_pct(trade: NormalizedTrade) -> float | None:
    if trade.trailing_activation_price is None or trade.entry_price <= 0:
        return None
    return (trade.trailing_activation_price - trade.entry_price) / trade.entry_price * 100


def _normalize_trade(
    row: sqlite3.Row,
    *,
    version: str,
    has_strategy_name: bool,
) -> NormalizedTrade:
    entry_price = float(row["entry_price"])
    exit_price = None if row["exit_price"] is None else float(row["exit_price"])

    if has_strategy_name:
        pnl_percent = (
            float(row["pnl_percent"])
            if row["pnl_percent"] is not None
            else _pnl_percent_from_prices(entry_price, exit_price)
        )
        pnl_usdc = (
            float(row["pnl_usdc"])
            if row["pnl_usdc"] is not None
            else _pnl_usdc_from_prices(entry_price, exit_price)
        )
        strategy_name = row["strategy_name"]
        entry_score = None
        entry_probability = None
    else:
        pnl_percent = (
            float(row["realized_profit_pct"])
            if row["realized_profit_pct"] is not None
            else _pnl_percent_from_prices(entry_price, exit_price)
        )
        pnl_usdc = _pnl_usdc_from_prices(entry_price, exit_price)
        strategy_name = "Shadow"
        entry_score = float(row["entry_score"])
        entry_probability = float(row["entry_probability"])

    closed_sort_key = row["closed_at"] or f"id:{row['id']:010d}"

    return NormalizedTrade(
        version=version,
        strategy_name=strategy_name,
        trade_id=int(row["id"]),
        pnl_percent=pnl_percent,
        pnl_usdc=pnl_usdc,
        exit_reason=row["exit_reason"],
        holding_time_seconds=float(row["holding_time_seconds"] or 0),
        entry_price=entry_price,
        exit_price=exit_price,
        max_profit_pct=None if row["max_profit_pct"] is None else float(row["max_profit_pct"]),
        realized_profit_pct=(
            None if row["realized_profit_pct"] is None else float(row["realized_profit_pct"])
        ),
        profit_left_on_table_pct=(
            None
            if row["profit_left_on_table_pct"] is None
            else float(row["profit_left_on_table_pct"])
        ),
        trailing_activation_price=(
            None
            if row["trailing_activation_price"] is None
            else float(row["trailing_activation_price"])
        ),
        entry_score=entry_score,
        entry_probability=entry_probability,
        closed_sort_key=closed_sort_key,
    )


def fetch_closed_trades(conn: sqlite3.Connection) -> list[NormalizedTrade]:
    trades: list[NormalizedTrade] = []
    for version, table, has_strategy_name in VERSION_TABLES:
        rows = conn.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE status = 'closed'
            ORDER BY closed_at ASC, id ASC
            """
        ).fetchall()
        for row in rows:
            trades.append(
                _normalize_trade(row, version=version, has_strategy_name=has_strategy_name)
            )
    return trades


def group_by_strategy(trades: list[NormalizedTrade]) -> list[StrategyPerformance]:
    grouped: dict[tuple[str, str], StrategyPerformance] = {}
    for trade in trades:
        key = (trade.version, trade.strategy_name)
        if key not in grouped:
            grouped[key] = StrategyPerformance(
                version=trade.version,
                strategy_name=trade.strategy_name,
            )
        grouped[key].trades.append(trade)

    def sort_key(item: StrategyPerformance) -> tuple[int, int, str, str]:
        version_order = {name: idx for idx, (name, _, _) in enumerate(VERSION_TABLES)}
        strategy_rank = (
            STRATEGY_ORDER.index(item.strategy_name)
            if item.strategy_name in STRATEGY_ORDER
            else len(STRATEGY_ORDER)
        )
        return (version_order.get(item.version, 99), strategy_rank, item.version, item.strategy_name)

    return sorted(grouped.values(), key=sort_key)


def _equity_curve(trades: list[NormalizedTrade]) -> list[tuple[int, float]]:
    ordered = sorted(trades, key=lambda t: t.closed_sort_key)
    cumulative = 0.0
    curve: list[tuple[int, float]] = []
    for index, trade in enumerate(ordered, start=1):
        cumulative += trade.pnl_percent
        curve.append((index, cumulative))
    return curve


def _max_drawdown_percent(trades: list[NormalizedTrade]) -> float:
    curve = _equity_curve(trades)
    if not curve:
        return 0.0
    peak = curve[0][1]
    max_dd = 0.0
    for _, equity in curve:
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _pnl_distribution(trades: list[NormalizedTrade]) -> list[tuple[str, int]]:
    counts = {label: 0 for label, _, _ in PNL_BUCKETS}
    for trade in trades:
        pnl = trade.pnl_percent
        for label, lower, upper in PNL_BUCKETS:
            if lower is None and pnl < 0:
                counts[label] += 1
                break
            if upper is None and pnl >= (lower or 0):
                counts[label] += 1
                break
            if lower is not None and upper is not None and lower <= pnl < upper:
                counts[label] += 1
                break
    return [(label, counts[label]) for label, _, _ in PNL_BUCKETS]


def _exit_reason_distribution(trades: list[NormalizedTrade]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for trade in trades:
        raw = trade.exit_reason or "UNKNOWN"
        label = EXIT_REASON_LABELS.get(raw, raw)
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _exit_type_counts(trades: list[NormalizedTrade]) -> tuple[int, int, int]:
    trailing = sum(1 for t in trades if t.exit_reason == "TRAILING_STOP")
    time_exits = sum(1 for t in trades if t.exit_reason == "TIME_STOP")
    stop_exits = sum(1 for t in trades if t.exit_reason == "STOP_LOSS")
    return trailing, time_exits, stop_exits


def _format_optional(value: float | None, *, suffix: str = "%", signed: bool = True) -> str:
    if value is None:
        return "—"
    if signed:
        return f"{value:+.2f}{suffix}"
    return f"{value:.2f}{suffix}"


def _format_profit_factor(value: float) -> str:
    if value == float("inf"):
        return "∞"
    return f"{value:.2f}"


def _format_strategy_section(perf: StrategyPerformance) -> list[str]:
    trades = perf.trades
    count = len(trades)
    lines = [f"Strategy: {perf.label}", ""]

    if count == 0:
        lines.append("(no closed trades)")
        lines.append("")
        return lines

    pnl_percents = perf.pnl_percents
    win_trades = perf.wins
    loss_trades = perf.losses
    trailing, time_exits, stop_exits = _exit_type_counts(trades)
    equity_curve = _equity_curve(trades)
    current_equity = equity_curve[-1][1] if equity_curve else 0.0
    max_dd = _max_drawdown_percent(trades)
    best = max(trades, key=lambda t: t.pnl_percent)
    worst = min(trades, key=lambda t: t.pnl_percent)

    lines.extend(
        [
            f"Trades:              {count}",
            f"Wins:                {len(win_trades)}",
            f"Losses:              {len(loss_trades)}",
            f"Win Rate:            {len(win_trades) / count:.1%}",
            "",
            f"Average Profit:      {_mean([t.pnl_percent for t in win_trades]):+.2f}%",
            f"Average Loss:        {_mean([t.pnl_percent for t in loss_trades]):+.2f}%",
            "",
            f"Average PnL:         {_mean(pnl_percents):+.2f}%",
            f"Median PnL:          {_median(pnl_percents):+.2f}%",
            "",
            f"Profit Factor:       {_format_profit_factor(_profit_factor(trades))}",
            "",
            f"Best Trade:          {best.pnl_percent:+.2f}%",
            f"Worst Trade:         {worst.pnl_percent:+.2f}%",
            "",
            f"Average Holding Time: {_mean([t.holding_time_seconds for t in trades]):.1f}s",
            "",
            f"Trailing exits:      {trailing}",
            f"Time exits:          {time_exits}",
            f"Stop exits:          {stop_exits}",
            "",
            f"Current Equity:      {current_equity:+.2f}%",
            f"Max Drawdown:        {max_dd:.2f}%",
        ]
    )

    if perf.version == "V4":
        activation_values = [
            value
            for trade in trades
            if (value := _activation_profit_pct(trade)) is not None
        ]
        lines.extend(
            [
                "",
                f"Average Score:       {_mean([t.entry_score for t in trades if t.entry_score is not None]):.2f}",
                f"Average Probability: {_mean([t.entry_probability for t in trades if t.entry_probability is not None]):.2f}",
                "",
                f"Average Entry Price: {_mean([t.entry_price for t in trades]):.4f}",
                "",
                f"Average Activation Profit: {_mean(activation_values):+.2f}%",
                f"Average Highest Profit:  {_mean([t.max_profit_pct for t in trades if t.max_profit_pct is not None]):+.2f}%",
                f"Average Left On Table:     {_mean([t.profit_left_on_table_pct for t in trades if t.profit_left_on_table_pct is not None]):+.2f}%",
                f"Average Realized Profit:   {_mean([t.realized_profit_pct for t in trades if t.realized_profit_pct is not None]):+.2f}%",
            ]
        )

    lines.extend(["", "PnL Distribution", ""])
    for label, bucket_count in _pnl_distribution(trades):
        lines.append(f"{label:<10} {bucket_count:>4}")

    lines.extend(["", "Exit Reasons", ""])
    for reason, reason_count in _exit_reason_distribution(trades):
        lines.append(f"{reason:<18} {reason_count:>4}")

    lines.extend(["", "Equity Curve", "", "Trade   Equity", ""])
    for trade_num, equity in equity_curve:
        lines.append(f"{trade_num:<7} {equity:+.1f}%")

    lines.append("")
    return lines


def build_version_summaries(trades: list[NormalizedTrade]) -> list[VersionSummary]:
    summaries: list[VersionSummary] = []
    for version, _, _ in VERSION_TABLES:
        version_trades = [t for t in trades if t.version == version]
        count = len(version_trades)
        if count == 0:
            summaries.append(
                VersionSummary(
                    version=version if version != "V4" else "V4 Shadow",
                    trades=0,
                    win_rate=0.0,
                    average_pnl_percent=0.0,
                    profit_factor=0.0,
                    max_drawdown_percent=0.0,
                )
            )
            continue
        wins = sum(1 for t in version_trades if t.pnl_percent > 0)
        summaries.append(
            VersionSummary(
                version=version if version != "V4" else "V4 Shadow",
                trades=count,
                win_rate=wins / count,
                average_pnl_percent=_mean([t.pnl_percent for t in version_trades]),
                profit_factor=_profit_factor(version_trades),
                max_drawdown_percent=_max_drawdown_percent(version_trades),
            )
        )
    return summaries


def _format_comparison_table(summaries: list[VersionSummary]) -> list[str]:
    headers = ("Strategy", "Trades", "Win %", "Avg PnL", "Profit Factor", "Max DD")
    rows = [
        (
            summary.version,
            str(summary.trades),
            f"{summary.win_rate:.1%}" if summary.trades else "—",
            f"{summary.average_pnl_percent:+.2f}%" if summary.trades else "—",
            _format_profit_factor(summary.profit_factor) if summary.trades else "—",
            f"{summary.max_drawdown_percent:.2f}%" if summary.trades else "—",
        )
        for summary in summaries
    ]

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows), 8 if i == 0 else 4)
        for i in range(len(headers))
    ]

    lines = ["Strategy Comparison", ""]
    header_line = "  ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))
    lines.append(header_line)
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(row))))
    lines.append("")
    return lines


def format_report(trades: list[NormalizedTrade]) -> str:
    sections = group_by_strategy(trades)
    lines = ["=== Trading Performance Report ===", ""]

    if not trades:
        lines.append("(no closed trades)")
        lines.extend(_format_comparison_table(build_version_summaries(trades)))
        return "\n".join(lines)

    for section in sections:
        lines.extend(_format_strategy_section(section))

    lines.extend(_format_comparison_table(build_version_summaries(trades)))
    return "\n".join(lines)


def print_report() -> None:
    init_db()
    with connect() as conn:
        trades = fetch_closed_trades(conn)
    print(format_report(trades))


def main() -> None:
    print_report()


if __name__ == "__main__":
    sys.exit(main() or 0)
