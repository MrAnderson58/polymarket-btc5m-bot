"""Backfill Early Reversion settlement for historical closed trades."""

from __future__ import annotations

import argparse
import sys

from bot.database import backfill_early_reversion_trades, connect, init_db


def run_backfill(*, dry_run: bool = False) -> int:
    init_db()

    with connect() as conn:
        pending = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM early_reversion_trades
            WHERE status = 'closed'
              AND (
                exit_price IS NULL
                OR pnl_percent IS NULL
                OR pnl_usdc IS NULL
                OR holding_time_seconds IS NULL
              )
            """
        ).fetchone()["count"]

        if dry_run:
            print(f"Would update {pending} closed trade(s)")
            return 0

        updated = backfill_early_reversion_trades(conn)
        conn.commit()

    print(f"Updated {updated} closed trade(s)")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill exit_price, pnl and holding_time for Early Reversion trades",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show how many trades would be updated without writing changes",
    )
    args = parser.parse_args()
    run_backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main() or 0)
