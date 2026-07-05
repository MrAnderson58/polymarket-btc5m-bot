"""Futures Signal Intelligence Research Layer.

Usage:
  python -m bot.research.futures check-source
  python -m bot.research.futures audit-data [--source CHANNEL]
  python -m bot.research.futures classify-sample [--source CHANNEL] [--limit N]
  python -m bot.research.futures template-discover [--source CHANNEL] [--limit N]
  python -m bot.research.futures lifecycle-audit [--source CHANNEL] [--limit N]
  python -m bot.research.futures parser-audit [--source CHANNEL] [--limit N]
  python -m bot.research.futures parse-sample [--source CHANNEL] [--limit N]
  python -m bot.research.futures parse [--source CHANNEL] [--limit N] [--parser-version V]
  python -m bot.research.futures snapshot
  python -m bot.research.futures outcomes
  python -m bot.research.futures report
  python -m bot.research.futures pipeline

Observe-only. No execution. No order placement.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Futures signal intelligence research")
    parser.add_argument(
        "command",
        choices=(
            "check-source",
            "audit-data",
            "classify-sample",
            "template-discover",
            "lifecycle-audit",
            "parser-audit",
            "parse-sample",
            "parse",
            "snapshot",
            "outcomes",
            "report",
            "pipeline",
        ),
        help="Research subcommand",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Filter by channel/source name (e.g. signalyp)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max source rows to read",
    )
    parser.add_argument(
        "--parser-version",
        default=None,
        help="Parser version (deterministic_v1 or deterministic_v2)",
    )
    parser.add_argument(
        "--stratified",
        action="store_true",
        help="Stratified sampling for parse-sample",
    )
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
                audit = audit_source_data(
                    research_conn, source, source_filter=args.source,
                )
                source.close()
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            print(render_audit_report(audit))
            return 0

        if args.command == "classify-sample":
            from bot.research.futures.classify_sample import classify_sample, render_classify_sample
            from bot.research.futures.source_reader import SourceConfigError

            limit = args.limit if args.limit is not None else 500
            try:
                report = classify_sample(
                    research_conn=research_conn,
                    source_filter=args.source,
                    limit=limit,
                )
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            print(render_classify_sample(report))
            return 0

        if args.command == "template-discover":
            from bot.research.futures.source_reader import SourceConfigError
            from bot.research.futures.template_discovery import discover_templates, render_template_report

            limit = args.limit if args.limit is not None else 2000
            try:
                report = discover_templates(
                    research_conn=research_conn,
                    source_filter=args.source,
                    limit=limit,
                )
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            print(render_template_report(report))
            return 0

        if args.command == "lifecycle-audit":
            from bot.research.futures.lifecycle_research import analyze_lifecycle, render_lifecycle_report
            from bot.research.futures.source_reader import SourceConfigError

            limit = args.limit if args.limit is not None else 5000
            try:
                report = analyze_lifecycle(
                    research_conn=research_conn,
                    source_filter=args.source,
                    limit=limit,
                )
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            print(render_lifecycle_report(report))
            return 0

        if args.command == "parser-audit":
            from bot.research.futures.parser_quality_audit import render_parser_audit, run_parser_audit
            from bot.research.futures.source_reader import SourceConfigError

            sample_size = args.limit if args.limit is not None else None
            try:
                kwargs = {"research_conn": research_conn, "source_filter": args.source}
                if sample_size is not None:
                    kwargs["sample_size"] = sample_size
                report = run_parser_audit(**kwargs)
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            print(render_parser_audit(report))
            return 0

        if args.command == "parse-sample":
            from bot.research.futures.config import PARSER_VERSION_V2
            from bot.research.futures.parse_sample import parse_sample, render_parse_sample
            from bot.research.futures.source_reader import SourceConfigError

            limit = args.limit if args.limit is not None else 30
            version = args.parser_version or PARSER_VERSION_V2
            try:
                report = parse_sample(
                    research_conn=research_conn,
                    source_filter=args.source,
                    limit=limit,
                    parser_version=version,
                    stratified=args.stratified,
                )
            except SourceConfigError as exc:
                print(f"SOURCE ERROR: {exc.reason_code}\n  {exc}", file=sys.stderr)
                return 1
            print(render_parse_sample(report))
            return 0

        if args.command == "parse":
            from bot.research.futures.parse_pipeline import parse_and_store_messages
            from bot.research.futures.source_reader import SourceConfigError

            try:
                stats = parse_and_store_messages(
                    research_conn,
                    limit=args.limit,
                    source_filter=args.source,
                    parser_version=args.parser_version,
                )
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
                p = parse_and_store_messages(
                    research_conn,
                    limit=args.limit,
                    source_filter=args.source,
                    parser_version=args.parser_version,
                )
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
