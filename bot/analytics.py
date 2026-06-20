"""Analytics for settled paper trades."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from bot.database import connect, init_db

DELTA_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("150-175", 150, 175),
    ("175-200", 175, 200),
    ("200-250", 200, 250),
    ("250+", 250, None),
)


@dataclass(frozen=True)
class TradeStats:
    count: int
    wins: int
    losses: int
    winrate: float
    pnl: float
    avg_abs_delta: float
    min_abs_delta: float
    max_abs_delta: float


@dataclass(frozen=True)
class BucketStats:
    label: str
    count: int
    wins: int
    losses: int
    winrate: float
    pnl: float


def _abs_delta(trade: sqlite3.Row) -> float:
    return abs(float(trade["btc_delta_at_entry"]))


def _bucket_label(abs_delta: float) -> str | None:
    for label, lower, upper in DELTA_BUCKETS:
        if upper is None:
            if abs_delta >= lower:
                return label
        elif lower <= abs_delta < upper:
            return label
    return None


def _compute_stats(trades: list[sqlite3.Row]) -> TradeStats:
    if not trades:
        return TradeStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    abs_deltas = [_abs_delta(t) for t in trades]
    wins = sum(1 for t in trades if t["outcome"] == "win")
    losses = sum(1 for t in trades if t["outcome"] == "loss")
    count = len(trades)
    pnl = sum(float(t["pnl_usdc"] or 0) for t in trades)

    return TradeStats(
        count=count,
        wins=wins,
        losses=losses,
        winrate=wins / count,
        pnl=pnl,
        avg_abs_delta=sum(abs_deltas) / count,
        min_abs_delta=min(abs_deltas),
        max_abs_delta=max(abs_deltas),
    )


def _compute_bucket_stats(trades: list[sqlite3.Row]) -> list[BucketStats]:
    grouped: dict[str, list[sqlite3.Row]] = {label: [] for label, _, _ in DELTA_BUCKETS}

    for trade in trades:
        label = _bucket_label(_abs_delta(trade))
        if label:
            grouped[label].append(trade)

    result: list[BucketStats] = []
    for label, _, _ in DELTA_BUCKETS:
        bucket_trades = grouped[label]
        wins = sum(1 for t in bucket_trades if t["outcome"] == "win")
        losses = sum(1 for t in bucket_trades if t["outcome"] == "loss")
        count = len(bucket_trades)
        pnl = sum(float(t["pnl_usdc"] or 0) for t in bucket_trades)
        winrate = wins / count if count else 0.0
        result.append(
            BucketStats(
                label=label,
                count=count,
                wins=wins,
                losses=losses,
                winrate=winrate,
                pnl=pnl,
            )
        )
    return result


def fetch_settled_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM virtual_trades
        WHERE status = 'settled'
        ORDER BY settled_at ASC, id ASC
        """
    ).fetchall()


def build_report(trades: list[sqlite3.Row]) -> tuple[TradeStats, list[BucketStats]]:
    return _compute_stats(trades), _compute_bucket_stats(trades)


def format_report(overall: TradeStats, buckets: list[BucketStats]) -> str:
    lines = [
        "=== Settled trades ===",
        f"count:           {overall.count}",
        f"winrate:         {overall.winrate:.1%}",
        f"total pnl:       {overall.pnl:+.4f} USDC",
        f"avg abs(delta):  {overall.avg_abs_delta:.2f}",
        f"min abs(delta):  {overall.min_abs_delta:.2f}",
        f"max abs(delta):  {overall.max_abs_delta:.2f}",
        "",
        "=== By abs(delta) at entry ===",
    ]

    if overall.count == 0:
        lines.append("(no settled trades)")
        return "\n".join(lines)

    for bucket in buckets:
        lines.extend(
            [
                f"[{bucket.label}]",
                f"  count:   {bucket.count}",
                f"  wins:    {bucket.wins}",
                f"  losses:  {bucket.losses}",
                f"  winrate: {bucket.winrate:.1%}",
                f"  pnl:     {bucket.pnl:+.4f} USDC",
                "",
            ]
        )

    return "\n".join(lines).rstrip()


def print_report() -> None:
    init_db()
    with connect() as conn:
        trades = fetch_settled_trades(conn)
    overall, buckets = build_report(trades)
    print(format_report(overall, buckets))


def main() -> None:
    print_report()


if __name__ == "__main__":
    sys.exit(main() or 0)
