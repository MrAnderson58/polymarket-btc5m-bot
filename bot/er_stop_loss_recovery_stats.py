"""Read-only STOP LOSS recovery analysis for Early Reversion v2 trades."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from bot.config import TRAILING_ACTIVATION_PROFIT
from bot.er_btc_direction_stats import _exit_ts

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
           holding_time_seconds
    FROM early_reversion_v2_trades
    WHERE exit_reason = 'STOP_LOSS'
      AND status = 'closed'
    ORDER BY entry_ts ASC
"""

RECOVERY_TIME_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("0–10 sec", 0.0, 10.0),
    ("10–20 sec", 10.0, 20.0),
    ("20–30 sec", 20.0, 30.0),
    ("30–60 sec", 30.0, 60.0),
    ("60–90 sec", 60.0, 90.0),
    (">90 sec", 90.0, None),
)

PRICE_BASIS_NOTE = (
    "Price basis: nearest market_checks bid after exit "
    f"(yes_bid / no_bid by side); trailing activation = entry + "
    f"{TRAILING_ACTIVATION_PROFIT:.2f}"
)


@dataclass(frozen=True)
class BidSnapshot:
    ts: int
    bid: float


@dataclass(frozen=True)
class TradeStopLossRecovery:
    trade_id: int
    market_slug: str
    strategy_name: str
    side: str
    entry_price: float
    stop_price: float
    exit_ts: int
    end_ts: int
    max_after_30s: float | None
    max_after_60s: float | None
    max_after_90s: float | None
    max_until_expiry: float | None
    time_to_max_sec: float | None
    time_to_entry_recovery_sec: float | None
    recovered_entry: bool
    recovered_entry_plus_001: bool
    recovered_entry_plus_002: bool
    recovered_trailing_activation: bool
    never_recovered: bool
    has_price_data: bool


@dataclass(frozen=True)
class StopLossRecoveryStats:
    total_stop_loss: int
    analyzed: int
    skipped_no_checks: int
    recovered_entry_count: int
    recovered_entry_rate: float
    recovered_entry_plus_001_count: int
    recovered_entry_plus_001_rate: float
    recovered_entry_plus_002_count: int
    recovered_entry_plus_002_rate: float
    recovered_trailing_count: int
    recovered_trailing_rate: float
    never_recovered_count: int
    never_recovered_rate: float
    avg_max_recovery_after_stop: float
    avg_time_to_entry_recovery_sec: float
    recovery_time_distribution: tuple[tuple[str, int], ...]
    trades: tuple[TradeStopLossRecovery, ...]


def _bid_column(side: str) -> str:
    return "yes_bid" if side == "YES" else "no_bid"


def _fetch_bid_snapshots_after_exit(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    side: str,
    exit_ts: int,
    end_ts: int,
) -> list[BidSnapshot]:
    column = _bid_column(side)
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
        (market_slug, exit_ts, end_ts),
    ).fetchall()
    snapshots: list[BidSnapshot] = []
    for row in rows:
        if row["bid"] is None:
            continue
        snapshots.append(BidSnapshot(ts=int(row["ts"]), bid=float(row["bid"])))
    return snapshots


def _max_bid_until(snapshots: list[BidSnapshot], *, until_ts: int) -> float | None:
    values = [snap.bid for snap in snapshots if snap.ts <= until_ts]
    if not values:
        return None
    return max(values)


def _max_bid_with_time(
    snapshots: list[BidSnapshot],
) -> tuple[float | None, int | None]:
    if not snapshots:
        return None, None
    best = max(snapshots, key=lambda snap: snap.bid)
    return best.bid, best.ts


def _first_recovery_time_sec(
    snapshots: list[BidSnapshot],
    *,
    exit_ts: int,
    threshold: float,
) -> float | None:
    for snap in snapshots:
        if snap.bid + 1e-9 >= threshold:
            return float(snap.ts - exit_ts)
    return None


def _analyze_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
) -> TradeStopLossRecovery:
    entry_price = float(trade["entry_price"])
    stop_price = float(trade["exit_price"])
    exit_ts = _exit_ts(trade)
    end_ts = int(trade["end_ts"])
    snapshots = _fetch_bid_snapshots_after_exit(
        conn,
        market_slug=trade["market_slug"],
        side=trade["side"],
        exit_ts=exit_ts,
        end_ts=end_ts,
    )

    max_until_expiry, max_ts = _max_bid_with_time(snapshots)
    max_after_30s = _max_bid_until(snapshots, until_ts=exit_ts + 30)
    max_after_60s = _max_bid_until(snapshots, until_ts=exit_ts + 60)
    max_after_90s = _max_bid_until(snapshots, until_ts=exit_ts + 90)

    trailing_threshold = entry_price + TRAILING_ACTIVATION_PROFIT
    recovered_entry = (
        max_until_expiry is not None and max_until_expiry + 1e-9 >= entry_price
    )
    recovered_entry_plus_001 = (
        max_until_expiry is not None and max_until_expiry + 1e-9 >= entry_price + 0.01
    )
    recovered_entry_plus_002 = (
        max_until_expiry is not None and max_until_expiry + 1e-9 >= entry_price + 0.02
    )
    recovered_trailing_activation = (
        max_until_expiry is not None and max_until_expiry + 1e-9 >= trailing_threshold
    )
    never_recovered = max_until_expiry is None or max_until_expiry + 1e-9 < entry_price

    time_to_max_sec = (
        float(max_ts - exit_ts) if max_ts is not None and max_until_expiry is not None else None
    )
    time_to_entry_recovery_sec = (
        _first_recovery_time_sec(snapshots, exit_ts=exit_ts, threshold=entry_price)
        if recovered_entry
        else None
    )

    return TradeStopLossRecovery(
        trade_id=int(trade["id"]),
        market_slug=trade["market_slug"],
        strategy_name=trade["strategy_name"],
        side=trade["side"],
        entry_price=entry_price,
        stop_price=stop_price,
        exit_ts=exit_ts,
        end_ts=end_ts,
        max_after_30s=max_after_30s,
        max_after_60s=max_after_60s,
        max_after_90s=max_after_90s,
        max_until_expiry=max_until_expiry,
        time_to_max_sec=time_to_max_sec,
        time_to_entry_recovery_sec=time_to_entry_recovery_sec,
        recovered_entry=recovered_entry,
        recovered_entry_plus_001=recovered_entry_plus_001,
        recovered_entry_plus_002=recovered_entry_plus_002,
        recovered_trailing_activation=recovered_trailing_activation,
        never_recovered=never_recovered,
        has_price_data=max_until_expiry is not None,
    )


def _recovery_time_bucket(seconds: float) -> str:
    for label, lower, upper in RECOVERY_TIME_BUCKETS:
        if upper is None:
            if seconds >= lower:
                return label
            continue
        if lower <= seconds < upper:
            return label
    return RECOVERY_TIME_BUCKETS[-1][0]


def fetch_stop_loss_recovery_stats(conn: sqlite3.Connection) -> StopLossRecoveryStats:
    rows = conn.execute(_ER_V2_STOP_LOSS_SELECT).fetchall()
    analyzed_trades: list[TradeStopLossRecovery] = []
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

    recovered_entry_count = sum(1 for t in analyzed_trades if t.recovered_entry)
    recovered_entry_plus_001_count = sum(
        1 for t in analyzed_trades if t.recovered_entry_plus_001
    )
    recovered_entry_plus_002_count = sum(
        1 for t in analyzed_trades if t.recovered_entry_plus_002
    )
    recovered_trailing_count = sum(
        1 for t in analyzed_trades if t.recovered_trailing_activation
    )
    never_recovered_count = sum(1 for t in analyzed_trades if t.never_recovered)

    recovery_amounts = [
        t.max_until_expiry - t.stop_price
        for t in analyzed_trades
        if t.max_until_expiry is not None
    ]
    avg_max_recovery = (
        sum(recovery_amounts) / len(recovery_amounts) if recovery_amounts else 0.0
    )

    entry_recovery_times = [
        t.time_to_entry_recovery_sec
        for t in analyzed_trades
        if t.time_to_entry_recovery_sec is not None
    ]
    avg_time_to_recovery = (
        sum(entry_recovery_times) / len(entry_recovery_times)
        if entry_recovery_times
        else 0.0
    )

    bucket_counts = {label: 0 for label, _, _ in RECOVERY_TIME_BUCKETS}
    for seconds in entry_recovery_times:
        bucket_counts[_recovery_time_bucket(seconds)] += 1

    return StopLossRecoveryStats(
        total_stop_loss=total,
        analyzed=analyzed,
        skipped_no_checks=skipped,
        recovered_entry_count=recovered_entry_count,
        recovered_entry_rate=_rate(recovered_entry_count),
        recovered_entry_plus_001_count=recovered_entry_plus_001_count,
        recovered_entry_plus_001_rate=_rate(recovered_entry_plus_001_count),
        recovered_entry_plus_002_count=recovered_entry_plus_002_count,
        recovered_entry_plus_002_rate=_rate(recovered_entry_plus_002_count),
        recovered_trailing_count=recovered_trailing_count,
        recovered_trailing_rate=_rate(recovered_trailing_count),
        never_recovered_count=never_recovered_count,
        never_recovered_rate=_rate(never_recovered_count),
        avg_max_recovery_after_stop=avg_max_recovery,
        avg_time_to_entry_recovery_sec=avg_time_to_recovery,
        recovery_time_distribution=tuple(
            (label, bucket_counts[label]) for label, _, _ in RECOVERY_TIME_BUCKETS
        ),
        trades=tuple(analyzed_trades),
    )


def _fmt_bool(value: bool) -> str:
    return "yes" if value else "no"


def format_stop_loss_recovery_summary(conn: sqlite3.Connection) -> str:
    stats = fetch_stop_loss_recovery_stats(conn)
    lines = [
        "STOP LOSS RECOVERY",
        "",
        PRICE_BASIS_NOTE,
        "",
        f"Total STOP_LOSS: {stats.total_stop_loss}",
        "",
    ]
    if stats.skipped_no_checks:
        lines.extend(
            [
                f"Skipped (no market_checks after exit): {stats.skipped_no_checks}",
                "",
            ]
        )

    lines.extend(
        [
            "Recovered to Entry:",
            "",
            f"{stats.recovered_entry_count}",
            "",
            f"{stats.recovered_entry_rate:.0%}",
            "",
            "Recovered to Entry+0.01:",
            "",
            f"{stats.recovered_entry_plus_001_count}",
            "",
            f"{stats.recovered_entry_plus_001_rate:.0%}",
            "",
            "Recovered to Entry+0.02:",
            "",
            f"{stats.recovered_entry_plus_002_count}",
            "",
            f"{stats.recovered_entry_plus_002_rate:.0%}",
            "",
            "Recovered to Trailing Activation:",
            "",
            f"{stats.recovered_trailing_count}",
            "",
            f"{stats.recovered_trailing_rate:.0%}",
            "",
            "Never Recovered:",
            "",
            f"{stats.never_recovered_count}",
            "",
            f"{stats.never_recovered_rate:.0%}",
            "",
            f"Average maximum recovery after stop: +{stats.avg_max_recovery_after_stop:.3f}",
            "",
            f"Average time to recovery: {stats.avg_time_to_entry_recovery_sec:.0f} sec",
            "",
            "Recovery within",
            "",
        ]
    )
    for label, count in stats.recovery_time_distribution:
        rate = count / stats.recovered_entry_count if stats.recovered_entry_count else 0.0
        lines.extend(
            [
                label,
                "",
                f"{count} ({rate:.0%} of recovered-to-entry)",
                "",
            ]
        )

    lines.extend(["Last 20 STOP_LOSS trades", ""])
    lines.append(
        "| market | entry | stop | max_after_stop | time_to_max | "
        "recovered_entry | recovered_0.01 | recovered_0.02 | recovered_trailing |"
    )
    lines.append(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    last_trades = sorted(stats.trades, key=lambda trade: trade.exit_ts)[-20:]
    for trade in last_trades:
        max_after = (
            f"{trade.max_until_expiry:.2f}"
            if trade.max_until_expiry is not None
            else "-"
        )
        time_to_max = (
            f"{trade.time_to_max_sec:.0f}s"
            if trade.time_to_max_sec is not None
            else "-"
        )
        lines.append(
            "| "
            f"{trade.market_slug} | "
            f"{trade.entry_price:.2f} | "
            f"{trade.stop_price:.2f} | "
            f"{max_after} | "
            f"{time_to_max} | "
            f"{_fmt_bool(trade.recovered_entry)} | "
            f"{_fmt_bool(trade.recovered_entry_plus_001)} | "
            f"{_fmt_bool(trade.recovered_entry_plus_002)} | "
            f"{_fmt_bool(trade.recovered_trailing_activation)} |"
        )

    return "\n".join(lines).rstrip()


def log_stop_loss_recovery_summary(conn: sqlite3.Connection) -> None:
    logger.info("\n%s", format_stop_loss_recovery_summary(conn))
