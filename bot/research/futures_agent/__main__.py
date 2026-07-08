"""Futures Intelligence Agent — observe-only research system.

Usage:
  python -m bot.research.futures_agent audit
  python -m bot.research.futures_agent migrate
  python -m bot.research.futures_agent stage3-migrate
  python -m bot.research.futures_agent ingest-research --source-table telegram_messages
  python -m bot.research.futures_agent research-classify-audit --sample-size 500
  python -m bot.research.futures_agent thesis-extract
  python -m bot.research.futures_agent research-stats
  python -m bot.research.futures_agent ingest --text "BTC LONG ..."
  python -m bot.research.futures_agent process-pending
  python -m bot.research.futures_agent snapshot --signal-id ID
  python -m bot.research.futures_agent snapshot-pending --limit 50
  python -m bot.research.futures_agent context-report --signal-id ID

Does NOT modify Polymarket execution, bidirectional, ER, or MTF collectors.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    from bot.research.futures_agent.env_bootstrap import bootstrap_config, resolve_agent_db_config

    bootstrap_config()

    parser = argparse.ArgumentParser(description="Futures Intelligence Agent (research only)")
    parser.add_argument(
        "command",
        choices=(
            "audit", "migrate", "ingest", "process-pending",
            "snapshot", "snapshot-pending", "context-report", "snapshot-audit",
            "telegram-poll", "telegram-diagnose", "stage3-audit",
            "stage3-migrate", "ingest-research", "research-stats",
            "thesis-extract", "research-classify-audit",
        ),
    )
    parser.add_argument("--text", default=None, help="Signal text for ingest")
    parser.add_argument("--signal-id", type=int, default=None, help="Signal id for snapshot/report")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process")
    parser.add_argument("--dry-run", action="store_true", help="Ingest without DB write")
    parser.add_argument("--notify", action="store_true", help="Send Telegram ack if configured")
    parser.add_argument(
        "--write",
        type=str,
        default=None,
        help="Write stage3-audit markdown to path",
    )
    parser.add_argument("--json", type=str, default=None, help="Write stage3-audit raw JSON")
    parser.add_argument(
        "--source-table",
        type=str,
        default="telegram_messages",
        help="Source table for ingest-research",
    )
    parser.add_argument("--channel", type=str, default=None, help="Filter by channel/source name")
    parser.add_argument("--start-ts", type=int, default=None, help="Min message epoch seconds")
    parser.add_argument("--end-ts", type=int, default=None, help="Max message epoch seconds")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=500,
        help="Sample size for research-classify-audit",
    )
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=None,
        help="Cap ingested posts per channel (source imbalance guard)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=2000,
        help="Chunk size for ingest-research",
    )
    args = parser.parse_args()

    cfg = resolve_agent_db_config()

    if args.command == "stage3-audit":
        from bot.research.futures_agent.env_bootstrap import project_root
        from bot.research.futures_agent.stage3_data_audit import main as stage3_audit_main
        argv = []
        out = args.write or str(project_root() / "docs" / "research" / "STAGE3_DATA_AUDIT.md")
        argv.extend(["--write", out])
        if args.json:
            argv.extend(["--json", args.json])
        return stage3_audit_main(argv)

    if args.command == "audit":
        from bot.research.futures_agent.audit import render_audit, run_architecture_audit
        print(render_audit(run_architecture_audit()))
        return 0

    from bot.research.futures_agent.db import agent_connection
    from bot.research.futures_agent.schema import apply_migrations

    if args.command in ("migrate", "stage3-migrate"):
        with agent_connection() as conn:
            applied = apply_migrations(conn)
        print(f"Backend: {cfg.backend} ({cfg.config_source})")
        if cfg.database_name:
            print(f"Database: {cfg.database_name}")
        elif cfg.sqlite_path:
            print(f"SQLite: {cfg.sqlite_path}")
        print(f"Migrations applied: {applied or ['already up to date']}")
        return 0

    if args.command == "ingest-research":
        from bot.research.futures_agent.research_ingest import ingest_research_posts

        with agent_connection() as conn:
            apply_migrations(conn)
            stats = ingest_research_posts(
                conn,
                source_table=args.source_table,
                channel=args.channel,
                start_ts=args.start_ts,
                end_ts=args.end_ts,
                limit=args.limit,
                chunk_size=args.chunk_size,
                max_per_source=args.max_per_source,
            )
        print(f"Backend: {cfg.backend}")
        print(
            f"scanned={stats.scanned} inserted={stats.inserted} "
            f"dup={stats.skipped_duplicate} hash_dup={stats.skipped_hash_duplicate} "
            f"source_cap={stats.skipped_source_cap} empty={stats.skipped_empty}"
        )
        if stats.per_channel:
            print("per_channel_inserted:", stats.per_channel)
        return 0

    if args.command == "thesis-extract":
        from bot.research.futures_agent.research_ingest import run_thesis_extract

        with agent_connection() as conn:
            apply_migrations(conn)
            stats = run_thesis_extract(
                conn,
                channel=args.channel,
                limit=args.limit,
            )
        print(f"posts_scanned={stats['posts_scanned']} theses={stats['theses_inserted']} "
              f"levels={stats['levels_inserted']} unresolved_skipped={stats['unresolved_skipped']}")
        return 0

    if args.command == "research-stats":
        from bot.research.futures_agent.research_stats import render_research_stats

        with agent_connection() as conn:
            apply_migrations(conn)
            print(render_research_stats(conn))
        return 0

    if args.command == "research-classify-audit":
        from bot.research.futures_agent.research_classify_audit import (
            render_classify_audit,
            run_classify_audit,
        )

        report = run_classify_audit(
            sample_size=args.sample_size,
            channel=args.channel,
        )
        print(render_classify_audit(report))
        return 0

    if args.command == "ingest":
        if not args.text:
            print("ERROR: --text required for ingest", file=sys.stderr)
            return 1
        if args.dry_run:
            from bot.research.futures.parser_v2 import parse_signal_v2
            r = parse_signal_v2(args.text)
            print(f"dry-run taxonomy={r.message_type.value} gate={r.passes_gate}")
            print(f"symbol={r.parsed.symbol} side={r.parsed.side}")
            return 0
        from bot.research.futures_agent.ingestion import ingest_from_cli
        from bot.research.futures_agent.pipeline import process_input
        from bot.research.futures_agent.responses import format_signal_received, send_telegram_message

        try:
            with agent_connection() as conn:
                apply_migrations(conn)
                ing = ingest_from_cli(conn, args.text)
                proc = process_input(conn, ing.input_id)
                msg = format_signal_received(conn, ing.input_id)
        except Exception as exc:
            print(f"ERROR: ingest failed; transaction rolled back: {exc}", file=sys.stderr)
            return 1

        dup = " duplicate" if ing.duplicate else ""
        print(
            f"Committed input={ing.input_id} signal={proc.signal_id}{dup} "
            f"-> {cfg.backend} gate={proc.passes_gate}"
        )
        print(msg)
        if args.notify:
            sent = send_telegram_message(msg)
            print(f"Telegram notify: {'sent' if sent else 'skipped/failed'}")
        return 0

    if args.command == "process-pending":
        from bot.research.futures_agent.pipeline import process_pending

        pending_limit = args.limit if args.limit is not None else 50
        with agent_connection() as conn:
            apply_migrations(conn)
            results = process_pending(conn, limit=pending_limit)
        print(f"Backend: {cfg.backend} ({cfg.config_source})")
        for r in results:
            print(
                f"input={r.input_id} signal={r.signal_id} "
                f"status={r.processing_status} gate={r.passes_gate} tax={r.taxonomy}"
            )
        print(f"Processed: {len(results)}")
        return 0

    if args.command == "snapshot":
        if args.signal_id is None:
            print("ERROR: --signal-id required for snapshot", file=sys.stderr)
            return 1
        from bot.research.futures_agent.snapshot import snapshot_signal

        with agent_connection() as conn:
            apply_migrations(conn)
            result = snapshot_signal(conn, args.signal_id)
        if result.skipped:
            print(f"Snapshot already exists for signal_id={args.signal_id}")
            return 0
        if not result.success:
            print(f"Snapshot failed for signal_id={args.signal_id}: {result.error}", file=sys.stderr)
            return 1
        print(
            f"Snapshot committed signal_id={args.signal_id} "
            f"quality={result.data_quality} research={result.research_label} "
            f"-> {cfg.backend}"
        )
        return 0

    if args.command == "snapshot-pending":
        from bot.research.futures_agent.snapshot import snapshot_pending

        snap_limit = args.limit if args.limit is not None else 50
        with agent_connection() as conn:
            apply_migrations(conn)
            results = snapshot_pending(conn, limit=snap_limit)
        print(f"Backend: {cfg.backend} ({cfg.config_source})")
        for r in results:
            status = "skipped" if r.skipped else ("ok" if r.success else f"fail:{r.error}")
            print(f"signal={r.signal_id} {status} quality={r.data_quality}")
        print(f"Snapshots processed: {len(results)}")
        return 0

    if args.command == "context-report":
        if args.signal_id is None:
            print("ERROR: --signal-id required for context-report", file=sys.stderr)
            return 1
        from bot.research.futures_agent.context_report import format_context_report

        with agent_connection() as conn:
            apply_migrations(conn)
            print(format_context_report(conn, args.signal_id))
        return 0

    if args.command == "snapshot-audit":
        if args.signal_id is None:
            print("ERROR: --signal-id required for snapshot-audit", file=sys.stderr)
            return 1
        from bot.research.futures_agent.snapshot_audit import format_snapshot_audit

        with agent_connection() as conn:
            apply_migrations(conn)
            print(format_snapshot_audit(conn, args.signal_id))
        return 0

    if args.command == "telegram-diagnose":
        from bot.research.futures_agent.telegram_inbound import render_diagnose, run_diagnose
        print(render_diagnose(run_diagnose()))
        return 0

    if args.command == "telegram-poll":
        import logging
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        from bot.research.futures_agent.telegram_inbound import run_poll_loop
        try:
            run_poll_loop()
        except KeyboardInterrupt:
            print("\nTelegram poll stopped.")
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
