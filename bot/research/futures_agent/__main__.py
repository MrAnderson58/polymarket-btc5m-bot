"""Futures Intelligence Agent — observe-only research system.

Usage:
  python -m bot.research.futures_agent audit
  python -m bot.research.futures_agent migrate
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
            "telegram-poll", "telegram-diagnose",
        ),
    )
    parser.add_argument("--text", default=None, help="Signal text for ingest")
    parser.add_argument("--signal-id", type=int, default=None, help="Signal id for snapshot/report")
    parser.add_argument("--limit", type=int, default=50, help="Max pending to process")
    parser.add_argument("--dry-run", action="store_true", help="Ingest without DB write")
    parser.add_argument("--notify", action="store_true", help="Send Telegram ack if configured")
    args = parser.parse_args()

    cfg = resolve_agent_db_config()

    if args.command == "audit":
        from bot.research.futures_agent.audit import render_audit, run_architecture_audit
        print(render_audit(run_architecture_audit()))
        return 0

    from bot.research.futures_agent.db import agent_connection
    from bot.research.futures_agent.schema import apply_migrations

    if args.command == "migrate":
        with agent_connection() as conn:
            applied = apply_migrations(conn)
        print(f"Backend: {cfg.backend} ({cfg.config_source})")
        if cfg.database_name:
            print(f"Database: {cfg.database_name}")
        elif cfg.sqlite_path:
            print(f"SQLite: {cfg.sqlite_path}")
        print(f"Migrations applied: {applied or ['already up to date']}")
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

        with agent_connection() as conn:
            apply_migrations(conn)
            results = process_pending(conn, limit=args.limit)
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

        with agent_connection() as conn:
            apply_migrations(conn)
            results = snapshot_pending(conn, limit=args.limit)
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
