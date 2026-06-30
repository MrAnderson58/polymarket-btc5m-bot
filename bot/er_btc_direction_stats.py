"""Read-only Early Reversion stats by BTC direction during trades."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from bot.er_stats import (
    STRATEGY_NAMES,
    _dedupe_strategy_trades,
    _enabled_strategy_names,
    _realized_pnl_percent,
)

logger = logging.getLogger(__name__)

BTC_FLAT_THRESHOLD_USD = 5.0

_ER_TRADE_TABLES: tuple[str, ...] = (
    "early_reversion_v2_trades",
    "early_reversion_v25_trades",
    "early_reversion_v3_trades",
)

_ER_BTC_TRADE_SELECT = """
    SELECT market_slug,
           strategy_name,
           status,
           entry_ts,
           entry_price,
           exit_price,
           pnl_percent,
           realized_profit_pct,
           holding_time_seconds
    FROM {table}
    WHERE strategy_name = ?
"""

BTC_DIRECTION_ORDER = ("BTC UP", "BTC DOWN", "BTC FLAT")


class BtcDirection(StrEnum):
    UP = "BTC UP"
    DOWN = "BTC DOWN"
    FLAT = "BTC FLAT"


@dataclass(frozen=True)
class BtcDirectionBucketStats:
    direction: str
    trades: int
    win_rate: float
    avg_pnl: float


@dataclass(frozen=True)
class StrategyBtcDirectionStats:
    strategy_name: str
    buckets: tuple[BtcDirectionBucketStats, ...]
    skipped_missing_btc: int
    avg_btc_move_profitable: float
    avg_btc_move_losing: float


@dataclass(frozen=True)
class TradeBtcContext:
    trade: sqlite3.Row
    pnl_percent: float
    btc_entry: float
    btc_exit: float
    btc_move: float
    direction: BtcDirection


def _fetch_strategy_trade_rows(
    conn: sqlite3.Connection,
    strategy_name: str,
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for table in _ER_TRADE_TABLES:
        rows.extend(
            conn.execute(
                _ER_BTC_TRADE_SELECT.format(table=table),
                (strategy_name,),
            ).fetchall()
        )
    return _dedupe_strategy_trades(rows)


def _nearest_btc_price(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    target_ts: int,
) -> float | None:
    row = conn.execute(
        """
        SELECT btc_price
        FROM market_checks
        WHERE market_slug = ?
        ORDER BY abs(cast(strftime('%s', checked_at) AS integer) - ?) ASC
        LIMIT 1
        """,
        (market_slug, target_ts),
    ).fetchone()
    if row is None:
        return None
    return float(row["btc_price"])


def _exit_ts(trade: sqlite3.Row) -> int:
    entry_ts = int(trade["entry_ts"])
    holding = trade["holding_time_seconds"]
    if holding is None:
        return entry_ts
    return entry_ts + int(round(float(holding)))


def _classify_btc_direction(btc_move: float) -> BtcDirection:
    if abs(btc_move) < BTC_FLAT_THRESHOLD_USD:
        return BtcDirection.FLAT
    if btc_move > 0:
        return BtcDirection.UP
    return BtcDirection.DOWN


def _trade_btc_context(conn: sqlite3.Connection, trade: sqlite3.Row) -> TradeBtcContext | None:
    market_slug = trade["market_slug"]
    entry_ts = int(trade["entry_ts"])
    exit_ts = _exit_ts(trade)
    btc_entry = _nearest_btc_price(conn, market_slug=market_slug, target_ts=entry_ts)
    btc_exit = _nearest_btc_price(conn, market_slug=market_slug, target_ts=exit_ts)
    if btc_entry is None or btc_exit is None:
        return None
    btc_move = btc_exit - btc_entry
    return TradeBtcContext(
        trade=trade,
        pnl_percent=_realized_pnl_percent(trade),
        btc_entry=btc_entry,
        btc_exit=btc_exit,
        btc_move=btc_move,
        direction=_classify_btc_direction(btc_move),
    )


def fetch_strategy_btc_direction_stats(
    conn: sqlite3.Connection,
    strategy_name: str,
) -> StrategyBtcDirectionStats:
    closed_trades = [
        trade
        for trade in _fetch_strategy_trade_rows(conn, strategy_name)
        if trade["status"] == "closed"
    ]

    by_direction: dict[str, list[TradeBtcContext]] = {
        direction: [] for direction in BTC_DIRECTION_ORDER
    }
    profitable_moves: list[float] = []
    losing_moves: list[float] = []
    skipped = 0

    for trade in closed_trades:
        context = _trade_btc_context(conn, trade)
        if context is None:
            skipped += 1
            continue
        by_direction[context.direction.value].append(context)
        if context.pnl_percent > 0:
            profitable_moves.append(context.btc_move)
        elif context.pnl_percent < 0:
            losing_moves.append(context.btc_move)

    buckets: list[BtcDirectionBucketStats] = []
    for direction in BTC_DIRECTION_ORDER:
        items = by_direction[direction]
        wins = sum(1 for item in items if item.pnl_percent > 0)
        pnls = [item.pnl_percent for item in items]
        buckets.append(
            BtcDirectionBucketStats(
                direction=direction,
                trades=len(items),
                win_rate=wins / len(items) if items else 0.0,
                avg_pnl=sum(pnls) / len(pnls) if pnls else 0.0,
            )
        )

    return StrategyBtcDirectionStats(
        strategy_name=strategy_name,
        buckets=tuple(buckets),
        skipped_missing_btc=skipped,
        avg_btc_move_profitable=_mean(profitable_moves),
        avg_btc_move_losing=_mean(losing_moves),
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def format_strategy_btc_direction_block(stats: StrategyBtcDirectionStats) -> list[str]:
    lines = [
        stats.strategy_name,
        "",
        f"Flat threshold: ${BTC_FLAT_THRESHOLD_USD:.0f} (BTC move between entry and exit)",
        "",
    ]
    if stats.skipped_missing_btc:
        lines.extend(
            [
                f"Skipped (no market_checks BTC): {stats.skipped_missing_btc}",
                "",
            ]
        )
    for bucket in stats.buckets:
        lines.extend(
            [
                bucket.direction,
                "",
                f"Trades: {bucket.trades}",
                "",
                f"Win Rate: {bucket.win_rate:.0%}",
                "",
                f"Avg PnL: {bucket.avg_pnl:+.1f}%",
                "",
            ]
        )
    lines.extend(
        [
            f"Average BTC move during profitable trades: {stats.avg_btc_move_profitable:+.2f}",
            "",
            f"Average BTC move during losing trades: {stats.avg_btc_move_losing:+.2f}",
            "",
        ]
    )
    return lines


def format_btc_direction_summary(conn: sqlite3.Connection) -> str:
    enabled = _enabled_strategy_names()
    if not enabled:
        enabled = STRATEGY_NAMES

    blocks: list[str] = [
        "BTC Direction Summary",
        "",
        "Price basis: nearest market_checks.btc_price at entry/exit timestamps",
        "",
    ]
    for index, strategy_name in enumerate(enabled):
        stats = fetch_strategy_btc_direction_stats(conn, strategy_name)
        block_lines = format_strategy_btc_direction_block(stats)
        if index > 0:
            blocks.append("")
        blocks.extend(block_lines)

    if not enabled:
        blocks.append("(no enabled strategies)")

    return "\n".join(blocks).rstrip()


def log_btc_direction_summary(conn: sqlite3.Connection) -> None:
    logger.info("\n%s", format_btc_direction_summary(conn))
