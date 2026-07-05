"""Futures Signal Intelligence Research Layer.

Usage:
  python -m bot.research.futures audit-data
  python -m bot.research.futures parse
  python -m bot.research.futures snapshot
  python -m bot.research.futures outcomes
  python -m bot.research.futures report
  python -m bot.research.futures pipeline   # parse + snapshot + outcomes

Observe-only. No execution. No order placement.
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="Futures signal intelligence research")
    parser.add_argument(
        "command",
        choices=(
            "audit-data",
            "parse",
            "snapshot",
            "outcomes",
            "report",
            "pipeline",
        ),
        help="Research subcommand",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit rows processed")
    args = parser.parse_args()

    from bot.database import connect, init_db
    from bot.research.futures.schema import ensure_tables

    init_db()
    with connect() as conn:
        ensure_tables(conn)

        if args.command == "audit-data":
            from bot.research.futures.source_audit import audit_source_data, render_audit_report
            audit = audit_source_data(conn)
            print(render_audit_report(audit))
            return 0

        if args.command == "parse":
            from bot.research.futures.parse_pipeline import parse_and_store_messages
            stats = parse_and_store_messages(conn, limit=args.limit)
            conn.commit()
            print(stats)
            return 0

        if args.command == "snapshot":
            from bot.research.futures.market_snapshot import snapshot_signals
            stats = snapshot_signals(conn, limit=args.limit)
            conn.commit()
            print(stats)
            return 0

        if args.command == "outcomes":
            from bot.research.futures.outcomes import evaluate_all_outcomes
            stats = evaluate_all_outcomes(conn, limit=args.limit)
            conn.commit()
            print(stats)
            return 0

        if args.command == "pipeline":
            from bot.research.futures.market_snapshot import snapshot_signals
            from bot.research.futures.outcomes import evaluate_all_outcomes
            from bot.research.futures.parse_pipeline import parse_and_store_messages
            p = parse_and_store_messages(conn, limit=args.limit)
            s = snapshot_signals(conn, limit=args.limit)
            o = evaluate_all_outcomes(conn, limit=args.limit)
            conn.commit()
            print({"parse": p, "snapshot": s, "outcomes": o})
            return 0

        if args.command == "report":
            from bot.research.futures.report import build_report, render_report
            report = build_report(conn)
            print(render_report(report))
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
