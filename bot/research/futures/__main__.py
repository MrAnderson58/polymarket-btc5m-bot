"""Futures Signal Intelligence Research Layer.

Usage:
  python -m bot.research.futures check-source
  python -m bot.research.futures audit-data
  python -m bot.research.futures parse
  python -m bot.research.futures snapshot
  python -m bot.research.futures outcomes
  python -m bot.research.futures report
  python -m bot.research.futures pipeline   # parse + snapshot + outcomes

Observe-only. No execution. No order placement.

Environment (production):
  FUTURES_SOURCE_DATABASE_URL=postgresql://user:pass@host:5432/dbname
  FUTURES_SOURCE_BACKEND=postgres          # auto | postgres | sqlite
  FUTURES_REQUIRE_POSTGRES=true          # fail if postgres unavailable
  FUTURES_RESEARCH_DATABASE_PATH=data/trades.db
"""

from __future__ import annotations

import argparse
import sys


def _open_research():
    from bot.database import connect, init_db
    from bot.research.futures.schema import ensure_tables

    init_db()
    conn = connect().__enter__()
    ensure_tables(conn)
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description="Futures signal intelligence research")
    parser.add_argument(
        "command",
        choices=(
            "check-source",
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

    if args.command == "check-source":
        from bot.database import connect, init_db
        from bot.research.futures.source_audit import check_source_connection, render_check_source

        init_db()
        with connect() as research_conn:
            result = check_source_connection(sqlite_conn=research_conn)
        print(render_check_source(result))
        return 0 if result.get("ok") else 1

    from bot.database import connect, init_db
    from bot.research.futures.schema import ensure_tables

    init_db()
    with connect() as research_conn:
        ensure_tables(research_conn)

        if args.command == "audit-data":
            from bot.research.futures.source_audit import audit_source_data, render_audit_report
            from bot.research.futures.source_reader import SourceConfigError, open_source_reader

            try:
                source = open_source_reader(sqlite_conn=research_conn)
                audit = audit_source_data(research_conn, source)
                source.close()
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            print(render_audit_report(audit))
            return 0

        if args.command == "parse":
            from bot.research.futures.parse_pipeline import parse_and_store_messages
            from bot.research.futures.source_reader import SourceConfigError

            try:
                stats = parse_and_store_messages(research_conn, limit=args.limit)
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            research_conn.commit()
            print(stats)
            return 0

        if args.command == "snapshot":
            from bot.research.futures.market_snapshot import snapshot_signals
            stats = snapshot_signals(research_conn, limit=args.limit)
            research_conn.commit()
            print(stats)
            return 0

        if args.command == "outcomes":
            from bot.research.futures.outcomes import evaluate_all_outcomes
            stats = evaluate_all_outcomes(research_conn, limit=args.limit)
            research_conn.commit()
            print(stats)
            return 0

        if args.command == "pipeline":
            from bot.research.futures.market_snapshot import snapshot_signals
            from bot.research.futures.outcomes import evaluate_all_outcomes
            from bot.research.futures.parse_pipeline import parse_and_store_messages
            from bot.research.futures.source_reader import SourceConfigError

            try:
                p = parse_and_store_messages(research_conn, limit=args.limit)
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            s = snapshot_signals(research_conn, limit=args.limit)
            o = evaluate_all_outcomes(research_conn, limit=args.limit)
            research_conn.commit()
            print({"parse": p, "snapshot": s, "outcomes": o})
            return 0

        if args.command == "report":
            from bot.research.futures.report import build_report, render_report
            report = build_report(research_conn)
            print(render_report(report))
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
