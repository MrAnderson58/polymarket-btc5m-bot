"""Read-only STOP LOSS short-window recovery for Early Reversion v2 trades."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from bot.config import TRAILING_ACTIVATION_PROFIT
from bot.er_btc_direction_stats import _exit_ts
from bot.er_stop_loss_recovery_stats import BidSnapshot, _bid_column

logger = logging.getLogger(__name__)

_ER_V2_STOP_LOSS_SELECT = """
    SELECT id,
           market_slug,
           side,
           strategy_name,
           entry_price,
           exit_price,
           entry_ts,
           end_ts,
           holding_time_seconds,
           pnl_percent
    FROM early_reversion_v2_trades
    WHERE exit_reason = 'STOP_LOSS'
      AND status = 'closed'
    ORDER BY entry_ts ASC
"""

HOLD_WINDOWS_SEC: tuple[int, ...] = (15, 30, 45, 60)
MAX_LOOKAHEAD_SEC = 60
SETTLEMENT_BID_THRESHOLD = 0.99

PRICE_BASIS_NOTE = (
    "Price basis: market_checks bid after stop (yes_bid / no_bid by side); "
    f"window capped at {MAX_LOOKAHEAD_SEC}s; settlement bids "
    f"(>={SETTLEMENT_BID_THRESHOLD:.2f}) excluded; trailing activation = "
    f"entry + {TRAILING_ACTIVATION_PROFIT:.2f}"
)


@dataclass(frozen=True)
class TradeShortRecovery:
    trade_id: int
    market_slug: str
    strategy_name: str
    side: str
    entry_price: float
    stop_price: float
    actual_pnl_percent: float
    exit_ts: int
    max_bid_15s: float | None
    max_bid_30s: float | None
    max_bid_45s: float | None
    max_bid_60s: float | None
    recovered_entry_15s: bool
    recovered_entry_30s: bool
    recovered_entry_45s: bool
    recovered_entry_60s: bool
    recovered_entry_plus_001_60s: bool
    recovered_entry_plus_002_60s: bool
    recovered_trailing_60s: bool
    never_recovered_60s: bool
    has_price_data: bool


@dataclass(frozen=True)
class AlternativeHoldStats:
    hold_sec: int
    trades: int
    win_rate: float
    avg_pnl: float
    profit_factor: float
    net_profit: float


@dataclass(frozen=True)
class StopLossShortRecoveryStats:
    total_stop_loss: int
    analyzed: int
    skipped_no_checks: int
    recovered_entry_15s_count: int
    recovered_entry_15s_rate: float
    recovered_entry_30s_count: int
    recovered_entry_30s_rate: float
    recovered_entry_45s_count: int
    recovered_entry_45s_rate: float
    recovered_entry_60s_count: int
    recovered_entry_60s_rate: float
    recovered_entry_plus_001_60s_count: int
    recovered_entry_plus_001_60s_rate: float
    recovered_entry_plus_002_60s_count: int
    recovered_entry_plus_002_60s_rate: float
    recovered_trailing_60s_count: int
    recovered_trailing_60s_rate: float
    never_recovered_60s_count: int
    never_recovered_60s_rate: float
    actual_stop_loss_net_profit: float
    actual_stop_loss_avg_pnl: float
    alternative_holds: tuple[AlternativeHoldStats, ...]
    trades: tuple[TradeShortRecovery, ...]


def _pnl_percent(entry_price: float, exit_price: float) -> float:
    return (exit_price - entry_price) / entry_price * 100


def _is_settlement_bid(bid: float) -> bool:
    return bid + 1e-9 >= SETTLEMENT_BID_THRESHOLD


def _fetch_short_bid_snapshots(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    side: str,
    exit_ts: int,
) -> list[BidSnapshot]:
    column = _bid_column(side)
    until_ts = exit_ts + MAX_LOOKAHEAD_SEC
    rows = conn.execute(
        f"""
        SELECT cast(strftime('%s', checked_at) AS integer) AS ts,
               {column} AS bid
        FROM market_checks
        WHERE market_slug = ?
          AND cast(strftime('%s', checked_at) AS integer) >= ?
          AND cast(strftime('%s', checked_at) AS integer) <= ?
        ORDER BY ts ASC
        """,
        (market_slug, exit_ts, until_ts),
    ).fetchall()
    snapshots: list[BidSnapshot] = []
    for row in rows:
        if row["bid"] is None:
            continue
        bid = float(row["bid"])
        if _is_settlement_bid(bid):
            continue
        snapshots.append(BidSnapshot(ts=int(row["ts"]), bid=bid))
    return snapshots


def _max_bid_within(
    snapshots: list[BidSnapshot],
    *,
    exit_ts: int,
    window_sec: int,
) -> float | None:
    until_ts = exit_ts + window_sec
    values = [snap.bid for snap in snapshots if snap.ts <= until_ts]
    if not values:
        return None
    return max(values)


def _recovered(max_bid: float | None, threshold: float) -> bool:
    return max_bid is not None and max_bid + 1e-9 >= threshold


def _analyze_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
) -> TradeShortRecovery:
    entry_price = float(trade["entry_price"])
    stop_price = float(trade["exit_price"])
    exit_ts = _exit_ts(trade)
    actual_pnl = (
        float(trade["pnl_percent"])
        if trade["pnl_percent"] is not None
        else _pnl_percent(entry_price, stop_price)
    )
    snapshots = _fetch_short_bid_snapshots(
        conn,
        market_slug=trade["market_slug"],
        side=trade["side"],
        exit_ts=exit_ts,
    )

    max_by_window = {
        window: _max_bid_within(snapshots, exit_ts=exit_ts, window_sec=window)
        for window in HOLD_WINDOWS_SEC
    }
    trailing_threshold = entry_price + TRAILING_ACTIVATION_PROFIT

    recovered_entry = {
        window: _recovered(max_by_window[window], entry_price)
        for window in HOLD_WINDOWS_SEC
    }

    return TradeShortRecovery(
        trade_id=int(trade["id"]),
        market_slug=trade["market_slug"],
        strategy_name=trade["strategy_name"],
        side=trade["side"],
        entry_price=entry_price,
        stop_price=stop_price,
        actual_pnl_percent=actual_pnl,
        exit_ts=exit_ts,
        max_bid_15s=max_by_window[15],
        max_bid_30s=max_by_window[30],
        max_bid_45s=max_by_window[45],
        max_bid_60s=max_by_window[60],
        recovered_entry_15s=recovered_entry[15],
        recovered_entry_30s=recovered_entry[30],
        recovered_entry_45s=recovered_entry[45],
        recovered_entry_60s=recovered_entry[60],
        recovered_entry_plus_001_60s=_recovered(max_by_window[60], entry_price + 0.01),
        recovered_entry_plus_002_60s=_recovered(max_by_window[60], entry_price + 0.02),
        recovered_trailing_60s=_recovered(max_by_window[60], trailing_threshold),
        never_recovered_60s=not recovered_entry[60],
        has_price_data=any(max_by_window[window] is not None for window in HOLD_WINDOWS_SEC),
    )


def _aggregate_hold_stats(
    trades: list[TradeShortRecovery],
    *,
    hold_sec: int,
) -> AlternativeHoldStats:
    pnls: list[float] = []
    for trade in trades:
        max_bid = {
            15: trade.max_bid_15s,
            30: trade.max_bid_30s,
            45: trade.max_bid_45s,
            60: trade.max_bid_60s,
        }[hold_sec]
        if max_bid is None:
            continue
        pnls.append(_pnl_percent(trade.entry_price, max_bid))

    if not pnls:
        return AlternativeHoldStats(
            hold_sec=hold_sec,
            trades=0,
            win_rate=0.0,
            avg_pnl=0.0,
            profit_factor=0.0,
            net_profit=0.0,
        )

    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    total_profit = sum(wins)
    total_loss = abs(sum(losses))
    return AlternativeHoldStats(
        hold_sec=hold_sec,
        trades=len(pnls),
        win_rate=len(wins) / len(pnls),
        avg_pnl=sum(pnls) / len(pnls),
        profit_factor=total_profit / total_loss if total_loss else float("inf"),
        net_profit=total_profit - total_loss,
    )


def fetch_stop_loss_short_recovery_stats(
    conn: sqlite3.Connection,
) -> StopLossShortRecoveryStats:
    rows = conn.execute(_ER_V2_STOP_LOSS_SELECT).fetchall()
    analyzed_trades: list[TradeShortRecovery] = []
    skipped = 0

    for trade in rows:
        result = _analyze_trade(conn, trade)
        if not result.has_price_data:
            skipped += 1
            continue
        analyzed_trades.append(result)

    total = len(rows)
    analyzed = len(analyzed_trades)

    def _rate(count: int) -> float:
        return count / analyzed if analyzed else 0.0

    actual_pnls = [trade.actual_pnl_percent for trade in analyzed_trades]

    return StopLossShortRecoveryStats(
        total_stop_loss=total,
        analyzed=analyzed,
        skipped_no_checks=skipped,
        recovered_entry_15s_count=sum(1 for t in analyzed_trades if t.recovered_entry_15s),
        recovered_entry_15s_rate=_rate(sum(1 for t in analyzed_trades if t.recovered_entry_15s)),
        recovered_entry_30s_count=sum(1 for t in analyzed_trades if t.recovered_entry_30s),
        recovered_entry_30s_rate=_rate(sum(1 for t in analyzed_trades if t.recovered_entry_30s)),
        recovered_entry_45s_count=sum(1 for t in analyzed_trades if t.recovered_entry_45s),
        recovered_entry_45s_rate=_rate(sum(1 for t in analyzed_trades if t.recovered_entry_45s)),
        recovered_entry_60s_count=sum(1 for t in analyzed_trades if t.recovered_entry_60s),
        recovered_entry_60s_rate=_rate(sum(1 for t in analyzed_trades if t.recovered_entry_60s)),
        recovered_entry_plus_001_60s_count=sum(
            1 for t in analyzed_trades if t.recovered_entry_plus_001_60s
        ),
        recovered_entry_plus_001_60s_rate=_rate(
            sum(1 for t in analyzed_trades if t.recovered_entry_plus_001_60s)
        ),
        recovered_entry_plus_002_60s_count=sum(
            1 for t in analyzed_trades if t.recovered_entry_plus_002_60s
        ),
        recovered_entry_plus_002_60s_rate=_rate(
            sum(1 for t in analyzed_trades if t.recovered_entry_plus_002_60s)
        ),
        recovered_trailing_60s_count=sum(1 for t in analyzed_trades if t.recovered_trailing_60s),
        recovered_trailing_60s_rate=_rate(sum(1 for t in analyzed_trades if t.recovered_trailing_60s)),
        never_recovered_60s_count=sum(1 for t in analyzed_trades if t.never_recovered_60s),
        never_recovered_60s_rate=_rate(sum(1 for t in analyzed_trades if t.never_recovered_60s)),
        actual_stop_loss_net_profit=sum(actual_pnls),
        actual_stop_loss_avg_pnl=sum(actual_pnls) / len(actual_pnls) if actual_pnls else 0.0,
        alternative_holds=tuple(
            _aggregate_hold_stats(analyzed_trades, hold_sec=hold_sec)
            for hold_sec in HOLD_WINDOWS_SEC
        ),
        trades=tuple(analyzed_trades),
    )


def _fmt_bool(value: bool) -> str:
    return "yes" if value else "no"


def _fmt_price(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "-"


def format_stop_loss_short_recovery_summary(conn: sqlite3.Connection) -> str:
    stats = fetch_stop_loss_short_recovery_stats(conn)
    lines = [
        "STOP LOSS SHORT RECOVERY",
        "",
        PRICE_BASIS_NOTE,
        "",
        f"Total STOP_LOSS: {stats.total_stop_loss}",
        "",
    ]
    if stats.skipped_no_checks:
        lines.extend(
            [
                f"Skipped (no market_checks within {MAX_LOOKAHEAD_SEC}s after exit): "
                f"{stats.skipped_no_checks}",
                "",
            ]
        )

    lines.extend(
        [
            "Recovered to Entry",
            "",
            "15 sec:",
            "",
            f"{stats.recovered_entry_15s_count}",
            "",
            f"{stats.recovered_entry_15s_rate:.0%}",
            "",
            "30 sec:",
            "",
            f"{stats.recovered_entry_30s_count}",
            "",
            f"{stats.recovered_entry_30s_rate:.0%}",
            "",
            "45 sec:",
            "",
            f"{stats.recovered_entry_45s_count}",
            "",
            f"{stats.recovered_entry_45s_rate:.0%}",
            "",
            "60 sec:",
            "",
            f"{stats.recovered_entry_60s_count}",
            "",
            f"{stats.recovered_entry_60s_rate:.0%}",
            "",
            "Recovered to Entry+0.01 (60s)",
            "",
            f"{stats.recovered_entry_plus_001_60s_count}",
            "",
            f"{stats.recovered_entry_plus_001_60s_rate:.0%}",
            "",
            "Recovered to Entry+0.02 (60s)",
            "",
            f"{stats.recovered_entry_plus_002_60s_count}",
            "",
            f"{stats.recovered_entry_plus_002_60s_rate:.0%}",
            "",
            "Recovered to Trailing Activation (60s)",
            "",
            f"{stats.recovered_trailing_60s_count}",
            "",
            f"{stats.recovered_trailing_60s_rate:.0%}",
            "",
            "Never recovered within 60s",
            "",
            f"{stats.never_recovered_60s_count}",
            "",
            f"{stats.never_recovered_60s_rate:.0%}",
            "",
            "ALTERNATIVE HOLD",
            "",
            "Actual STOP_LOSS (closed at stop)",
            "",
            f"Avg PnL: {stats.actual_stop_loss_avg_pnl:+.2f}%",
            "",
            f"Net Profit: {stats.actual_stop_loss_net_profit:+.1f}%",
            "",
        ]
    )

    for hold in stats.alternative_holds:
        pf = f"{hold.profit_factor:.2f}" if hold.profit_factor != float("inf") else "inf"
        lines.extend(
            [
                f"Hold {hold.hold_sec} sec (best bid exit)",
                "",
                f"Trades: {hold.trades}",
                "",
                f"Win Rate: {hold.win_rate:.0%}",
                "",
                f"Avg PnL: {hold.avg_pnl:+.2f}%",
                "",
                f"Profit Factor: {pf}",
                "",
                f"Net Profit: {hold.net_profit:+.1f}%",
                "",
                f"vs actual stop Net Profit: {hold.net_profit - stats.actual_stop_loss_net_profit:+.1f}%",
                "",
            ]
        )

    lines.extend(["Last 30 STOP_LOSS trades", ""])
    lines.append(
        "| market | entry | stop | max_bid_15s | max_bid_30s | max_bid_45s | "
        "max_bid_60s | recovered_entry_15 | recovered_entry_30 | "
        "recovered_entry_45 | recovered_entry_60 | recovered_trailing_60 |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    last_trades = sorted(stats.trades, key=lambda trade: trade.exit_ts)[-30:]
    for trade in last_trades:
        lines.append(
            "| "
            f"{trade.market_slug} | "
            f"{trade.entry_price:.2f} | "
            f"{trade.stop_price:.2f} | "
            f"{_fmt_price(trade.max_bid_15s)} | "
            f"{_fmt_price(trade.max_bid_30s)} | "
            f"{_fmt_price(trade.max_bid_45s)} | "
            f"{_fmt_price(trade.max_bid_60s)} | "
            f"{_fmt_bool(trade.recovered_entry_15s)} | "
            f"{_fmt_bool(trade.recovered_entry_30s)} | "
            f"{_fmt_bool(trade.recovered_entry_45s)} | "
            f"{_fmt_bool(trade.recovered_entry_60s)} | "
            f"{_fmt_bool(trade.recovered_trailing_60s)} |"
        )

    return "\n".join(lines).rstrip()


def log_stop_loss_short_recovery_summary(conn: sqlite3.Connection) -> None:
    logger.info("\n%s", format_stop_loss_short_recovery_summary(conn))
