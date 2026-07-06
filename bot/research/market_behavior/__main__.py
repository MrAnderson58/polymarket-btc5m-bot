"""Market behavior research CLI.

Usage:
  python -m bot.research.market_behavior report
  python -m bot.research.market_behavior report --limit 50

Read-only. Analyzes existing v4_shadow_observations history.
Does not modify trading logic, collector, or execution tables.
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe-only Polymarket market behavior research",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    report_p = sub.add_parser("report", help="Analyze snapshots and print report")
    report_p.add_argument(
        "--min-obs", type=int, default=None,
        help="Minimum observations per market (default from config)",
    )
    report_p.add_argument(
        "--limit", type=int, default=None,
        help="Max markets to analyze (most recent slug order)",
    )
    report_p.add_argument(
        "--no-persist", action="store_true",
        help="Compute report without writing mb_* tables",
    )

    args = parser.parse_args()

    if args.command == "report":
        from bot.database import connect, init_db
        from bot.research.market_behavior.engine import run_analysis
        from bot.research.market_behavior.report import render_report
        from bot.research.market_behavior.schema import ensure_tables

        init_db()
        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            report = run_analysis(
                conn,
                min_obs=args.min_obs,
                market_limit=args.limit,
                persist=not args.no_persist,
            )
        print(render_report(report))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
