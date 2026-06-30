"""Read-only trailing stop activation statistics for Early Reversion."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from bot.config import TRAILING_ACTIVATION_PROFIT
from bot.er_stats import STRATEGY_NAMES, _enabled_strategy_names

logger = logging.getLogger(__name__)

_ER_TRADE_TABLES: tuple[str, ...] = (
    "early_reversion_v2_trades",
    "early_reversion_v25_trades",
    "early_reversion_v3_trades",
)

_ER_TRAILING_TRADE_SELECT = """
    SELECT market_slug,
           strategy_name,
           status,
           entry_ts,
           entry_price,
           exit_price,
           exit_reason,
           max_price_seen,
           trailing_enabled,
           trailing_active,
           trailing_activation_price,
           highest_price
    FROM {table}
    WHERE strategy_name = ?
"""

MAX_EXCURSION_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("0.00–0.01", 0.0, 0.01),
    ("0.01–0.02", 0.01, 0.02),
    ("0.02–0.03", 0.02, 0.03),
    ("0.03–0.04", 0.03, 0.04),
    ("0.04+", 0.04, None),
)

EXCURSION_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("+0.02", 0.02),
    ("+0.03", 0.03),
    ("+0.04", 0.04),
)

PRICE_BASIS_NOTE = (
    "Price basis: bid peak via max_price_seen; activation threshold from "
    f"TRAILING_ACTIVATION_PROFIT={TRAILING_ACTIVATION_PROFIT:.2f}"
)


@dataclass(frozen=True)
class TradeDebugRow:
    market_slug: str
    entry_price: float
    highest_price: float | None
    trailing_activation_price: float | None
    trailing_active: bool
    exit_reason: str | None
    bid_peak: float | None
    bid_excursion: float


@dataclass(frozen=True)
class MaxExcursionDistribution:
    buckets: tuple[tuple[str, int], ...]
    threshold_counts: tuple[tuple[str, int], ...]
    threshold_rates: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class TrailingStrategyStats:
    strategy_name: str
    entries: int
    closed_trades: int
    trailing_activated: int
    activation_rate: float
    avg_peak_before_exit: float
    avg_peak_after_activation: float
    exited_by_trailing: int
    exited_by_trailing_legacy: int
    exited_by_stop_loss: int
    exited_by_time_stop: int
    avg_profit_at_activation: float
    max_excursion: MaxExcursionDistribution
    reach_at_activation_threshold: int
    warnings: tuple[str, ...]
    debug_rows: tuple[TradeDebugRow, ...]


def _trailing_row_quality(row: sqlite3.Row) -> int:
    score = 0
    if row["trailing_activation_price"] is not None:
        score += 8
    if bool(row["trailing_active"]):
        score += 4
    if row["highest_price"] is not None:
        score += 2
    if row["exit_reason"] is not None:
        score += 1
    if bool(row["trailing_enabled"]):
        score += 1
    return score


def _dedupe_trailing_trades(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Prefer the version row with the richest trailing metadata."""
    by_key: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (row["market_slug"], row["strategy_name"])
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = row
            continue
        row_quality = _trailing_row_quality(row)
        existing_quality = _trailing_row_quality(existing)
        if row_quality > existing_quality:
            by_key[key] = row
        elif row_quality == existing_quality and int(row["entry_ts"]) >= int(existing["entry_ts"]):
            by_key[key] = row
    return list(by_key.values())


def _fetch_strategy_trailing_rows(
    conn: sqlite3.Connection,
    strategy_name: str,
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for table in _ER_TRADE_TABLES:
        rows.extend(
            conn.execute(
                _ER_TRAILING_TRADE_SELECT.format(table=table),
                (strategy_name,),
            ).fetchall()
        )
    return _dedupe_trailing_trades(rows)


def _bid_peak(trade: sqlite3.Row) -> float | None:
    peak = trade["max_price_seen"]
    if peak is None:
        return None
    return float(peak)


def _bid_excursion(entry_price: float, trade: sqlite3.Row) -> float:
    peak = _bid_peak(trade)
    if peak is None:
        return 0.0
    return max(0.0, peak - entry_price)


def _trailing_activation_reached(
    entry_price: float,
    trade: sqlite3.Row,
    *,
    threshold: float = TRAILING_ACTIVATION_PROFIT,
) -> bool:
    """Trailing activation threshold reached on bid peak (max_price_seen)."""
    return _bid_excursion(entry_price, trade) + 1e-9 >= threshold


def _trailing_was_activated(
    entry_price: float,
    trade: sqlite3.Row,
    *,
    threshold: float = TRAILING_ACTIVATION_PROFIT,
) -> bool:
    """Activated if bid peak reached threshold or activation was persisted."""
    if bool(trade["trailing_active"]) or trade["trailing_activation_price"] is not None:
        return True
    return _trailing_activation_reached(entry_price, trade, threshold=threshold)


def _profit_at_activation(entry_price: float, trade: sqlite3.Row) -> float | None:
    activation_bid = trade["trailing_activation_price"]
    if activation_bid is not None:
        return float(activation_bid) - entry_price
    if _trailing_activation_reached(entry_price, trade):
        return TRAILING_ACTIVATION_PROFIT
    return None


def _peak_after_activation(entry_price: float, trade: sqlite3.Row) -> float | None:
    if not _trailing_was_activated(entry_price, trade):
        return None
    highest = trade["highest_price"]
    if highest is not None:
        return max(0.0, float(highest) - entry_price)
    peak = _bid_peak(trade)
    if peak is not None:
        return max(0.0, peak - entry_price)
    return None


def _exited_by_trailing(entry_price: float, trade: sqlite3.Row) -> bool:
    if trade["exit_reason"] != "TRAILING_STOP":
        return False
    return _trailing_was_activated(entry_price, trade)


def _exited_by_trailing_legacy(entry_price: float, trade: sqlite3.Row) -> bool:
    if trade["exit_reason"] != "TRAILING_STOP":
        return False
    return not _trailing_was_activated(entry_price, trade)


def _bucket_excursion(excursion: float) -> str:
    for label, lower, upper in MAX_EXCURSION_BUCKETS:
        if upper is None:
            if excursion >= lower:
                return label
            continue
        if lower <= excursion < upper:
            return label
    return MAX_EXCURSION_BUCKETS[-1][0]


def _build_max_excursion_distribution(excursions: list[float]) -> MaxExcursionDistribution:
    counts = {label: 0 for label, _, _ in MAX_EXCURSION_BUCKETS}
    for excursion in excursions:
        counts[_bucket_excursion(excursion)] += 1

    closed = len(excursions)
    threshold_counts: list[tuple[str, int]] = []
    threshold_rates: list[tuple[str, float]] = []
    for label, threshold in EXCURSION_THRESHOLDS:
        reached = sum(1 for value in excursions if value + 1e-9 >= threshold)
        threshold_counts.append((label, reached))
        threshold_rates.append((label, reached / closed if closed else 0.0))

    return MaxExcursionDistribution(
        buckets=tuple((label, counts[label]) for label, _, _ in MAX_EXCURSION_BUCKETS),
        threshold_counts=tuple(threshold_counts),
        threshold_rates=tuple(threshold_rates),
    )


def _check_invariants(
    *,
    entries: int,
    closed_trades: int,
    trailing_activated: int,
    exited_by_trailing: int,
    reach_at_activation_threshold: int,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if trailing_activated > entries:
        warnings.append(
            f"Trailing Activated ({trailing_activated}) > Entries ({entries})"
        )
    if exited_by_trailing > trailing_activated:
        warnings.append(
            f"Exited By Trailing ({exited_by_trailing}) > "
            f"Trailing Activated ({trailing_activated})"
        )
    if reach_at_activation_threshold < trailing_activated:
        warnings.append(
            f"Reach {TRAILING_ACTIVATION_PROFIT:+.2f} count "
            f"({reach_at_activation_threshold}) < "
            f"Trailing Activated ({trailing_activated})"
        )
    if trailing_activated > closed_trades:
        warnings.append(
            f"Trailing Activated ({trailing_activated}) > "
            f"closed sample ({closed_trades})"
        )
    return tuple(warnings)


def _debug_row(entry_price: float, trade: sqlite3.Row) -> TradeDebugRow:
    peak = _bid_peak(trade)
    return TradeDebugRow(
        market_slug=trade["market_slug"],
        entry_price=entry_price,
        highest_price=(
            float(trade["highest_price"]) if trade["highest_price"] is not None else None
        ),
        trailing_activation_price=(
            float(trade["trailing_activation_price"])
            if trade["trailing_activation_price"] is not None
            else None
        ),
        trailing_active=bool(trade["trailing_active"]),
        exit_reason=trade["exit_reason"],
        bid_peak=peak,
        bid_excursion=_bid_excursion(entry_price, trade),
    )


def fetch_trailing_strategy_stats(
    conn: sqlite3.Connection,
    strategy_name: str,
) -> TrailingStrategyStats:
    trades = _fetch_strategy_trailing_rows(conn, strategy_name)
    closed_trades = [trade for trade in trades if trade["status"] == "closed"]
    closed_trades_sorted = sorted(
        closed_trades,
        key=lambda row: int(row["entry_ts"]),
        reverse=True,
    )

    activated = 0
    peaks_before_exit: list[float] = []
    peaks_after_activation: list[float] = []
    profits_at_activation: list[float] = []
    exited_by_trailing = 0
    exited_by_trailing_legacy = 0
    exited_by_stop_loss = 0
    exited_by_time_stop = 0

    for trade in closed_trades:
        entry_price = float(trade["entry_price"])
        excursion = _bid_excursion(entry_price, trade)
        peaks_before_exit.append(excursion)

        if _trailing_was_activated(entry_price, trade):
            activated += 1
            profit = _profit_at_activation(entry_price, trade)
            if profit is not None:
                profits_at_activation.append(profit)
            peak_after = _peak_after_activation(entry_price, trade)
            if peak_after is not None:
                peaks_after_activation.append(peak_after)

        if _exited_by_trailing(entry_price, trade):
            exited_by_trailing += 1
        elif _exited_by_trailing_legacy(entry_price, trade):
            exited_by_trailing_legacy += 1

        reason = trade["exit_reason"]
        if reason == "STOP_LOSS":
            exited_by_stop_loss += 1
        elif reason == "TIME_STOP":
            exited_by_time_stop += 1

    closed_count = len(closed_trades)
    max_excursion = _build_max_excursion_distribution(peaks_before_exit)
    reach_activation_count = sum(
        1
        for value in peaks_before_exit
        if value + 1e-9 >= TRAILING_ACTIVATION_PROFIT
    )

    warnings = _check_invariants(
        entries=len(trades),
        closed_trades=closed_count,
        trailing_activated=activated,
        exited_by_trailing=exited_by_trailing,
        reach_at_activation_threshold=reach_activation_count,
    )

    return TrailingStrategyStats(
        strategy_name=strategy_name,
        entries=len(trades),
        closed_trades=closed_count,
        trailing_activated=activated,
        activation_rate=activated / closed_count if closed_count else 0.0,
        avg_peak_before_exit=_mean(peaks_before_exit),
        avg_peak_after_activation=_mean(peaks_after_activation),
        exited_by_trailing=exited_by_trailing,
        exited_by_trailing_legacy=exited_by_trailing_legacy,
        exited_by_stop_loss=exited_by_stop_loss,
        exited_by_time_stop=exited_by_time_stop,
        avg_profit_at_activation=_mean(profits_at_activation),
        max_excursion=max_excursion,
        reach_at_activation_threshold=reach_activation_count,
        warnings=warnings,
        debug_rows=tuple(
            _debug_row(float(trade["entry_price"]), trade)
            for trade in closed_trades_sorted[:10]
        ),
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _format_debug_table(rows: tuple[TradeDebugRow, ...]) -> list[str]:
    if not rows:
        return ["Sample trades: (none)", ""]
    lines = [
        "Sample trades (latest 10 closed):",
        "",
        "market | entry | bid_peak | excursion | highest | activation | active | exit",
        "",
    ]
    for row in rows:
        lines.append(
            f"{row.market_slug} | "
            f"{row.entry_price:.3f} | "
            f"{row.bid_peak if row.bid_peak is not None else '-'} | "
            f"{row.bid_excursion:.3f} | "
            f"{row.highest_price if row.highest_price is not None else '-'} | "
            f"{row.trailing_activation_price if row.trailing_activation_price is not None else '-'} | "
            f"{int(row.trailing_active)} | "
            f"{row.exit_reason or '-'}"
        )
        lines.append("")
    return lines


def format_trailing_strategy_block(stats: TrailingStrategyStats) -> list[str]:
    activation_threshold_label = f"+{TRAILING_ACTIVATION_PROFIT:.2f}"

    lines = [
        stats.strategy_name,
        "",
        PRICE_BASIS_NOTE,
        "",
        f"Sample: {stats.closed_trades} closed trades (of {stats.entries} entries)",
        "",
        f"Entries: {stats.entries}",
        "",
        f"Trailing Activated: {stats.trailing_activated}",
        "",
        f"Activation Rate: {stats.activation_rate:.1%}",
        "",
        f"Average Peak Before Exit: {stats.avg_peak_before_exit:.3f}",
        "",
        f"Average Peak After Activation: {stats.avg_peak_after_activation:.3f}",
        "",
        f"Exited By Trailing: {stats.exited_by_trailing}",
        "",
        f"Exited By Trailing (legacy): {stats.exited_by_trailing_legacy}",
        "",
        f"Exited By Stop Loss: {stats.exited_by_stop_loss}",
        "",
        f"Exited By Time Stop: {stats.exited_by_time_stop}",
        "",
        f"Average Profit At Activation: {stats.avg_profit_at_activation:.3f}",
        "",
        "Max Excursion",
        "",
    ]
    for label, count in stats.max_excursion.buckets:
        lines.extend([label, "", str(count), ""])
    for label, count in stats.max_excursion.threshold_counts:
        rate = next(r for l, r in stats.max_excursion.threshold_rates if l == label)
        lines.extend([f"Reach {label}: {count} ({rate:.1%})", ""])
    lines.extend(
        [
            f"Reach {activation_threshold_label} (activation threshold): "
            f"{stats.reach_at_activation_threshold}",
            "",
        ]
    )
    if stats.warnings:
        lines.extend(["WARNING", ""])
        for warning in stats.warnings:
            lines.extend([warning, ""])
    lines.extend(_format_debug_table(stats.debug_rows))
    return lines


def format_trailing_summary(conn: sqlite3.Connection) -> str:
    enabled = _enabled_strategy_names()
    if not enabled:
        enabled = STRATEGY_NAMES

    blocks: list[str] = ["Trailing Summary", ""]
    for index, strategy_name in enumerate(enabled):
        stats = fetch_trailing_strategy_stats(conn, strategy_name)
        block_lines = format_trailing_strategy_block(stats)
        if index > 0:
            blocks.append("")
        blocks.extend(block_lines)

    if not enabled:
        blocks.append("(no enabled strategies)")

    return "\n".join(blocks).rstrip()


def log_trailing_summary(conn: sqlite3.Connection) -> None:
    summary = format_trailing_summary(conn)
    logger.info("\n%s", summary)
    for strategy_name in _enabled_strategy_names() or STRATEGY_NAMES:
        stats = fetch_trailing_strategy_stats(conn, strategy_name)
        for warning in stats.warnings:
            logger.warning("Trailing Summary invariant: %s (%s)", warning, strategy_name)
