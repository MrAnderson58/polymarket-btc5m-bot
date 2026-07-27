"""Phase E.1/E.2/E.2.1/E.2.2 CLI."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from bot.research.market_events.db import (
    ensure_db_initialized,
    market_events_connection,
    market_events_readonly_connection,
)
from bot.research.market_events.event_schema import apply_migrations


def _parse_symbols(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def _normalize_trade_argv(argv: list[str] | None) -> list[str] | None:
    """Map `trade import|list|report|similar` → `trade-<action>` for flat argparse."""
    if not argv:
        return argv
    if argv[0] != "trade":
        return argv
    if len(argv) == 1:
        return ["trade-help", *argv[1:]]
    action = argv[1].strip().lower()
    if action in ("import", "list", "report", "similar", "help"):
        mapped = "trade-help" if action == "help" else f"trade-{action}"
        return [mapped, *argv[2:]]
    return ["trade-help", *argv[1:]]


def _audit_s42_db_path(*, command: str) -> int:
    """FIX-S4.3B: print DB path diagnostics before S4 CLI commands.

    Returns 0 if S4.2 review columns are present (or backend is not sqlite).
    Returns 1 with a clear migrate hint if schema is older than S4.2.
    Does not change learning / Decision / G3 logic.
    """
    from bot.research.market_events.db_config import resolve_market_events_db_config
    from bot.research.market_events.event_schema import SCHEMA_VERSION

    cfg = resolve_market_events_db_config()
    path = cfg.sqlite_path
    exists = path.exists()
    user_version: int | str | None = None
    max_version: int | str | None = None
    has_review_status = False
    has_review_type = False

    if cfg.backend == "sqlite":
        if exists:
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
                    try:
                        max_version = conn.execute(
                            "SELECT MAX(version) FROM market_events_migrations",
                        ).fetchone()[0]
                    except sqlite3.OperationalError:
                        max_version = None
                    try:
                        cols = {
                            str(r[1])
                            for r in conn.execute(
                                "PRAGMA table_info(market_events_signal_learning_s40_reviews)",
                            )
                        }
                        has_review_status = "review_status" in cols
                        has_review_type = "review_type" in cols
                    except sqlite3.OperationalError:
                        has_review_status = False
                        has_review_type = False
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                print(f"[DEBUG] DB Path Audit ({command}): open failed: {exc}", file=sys.stderr)
        else:
            has_review_status = False
            has_review_type = False
    else:
        # Non-sqlite: still print resolved config; column check is sqlite-focused.
        has_review_status = True
        has_review_type = True

    print(f"[DEBUG] DB Path Audit ({command})", file=sys.stderr)
    print(f"DB path: {path}", file=sys.stderr)
    print(f"Schema version: {SCHEMA_VERSION}", file=sys.stderr)
    print(f"SQLite file exists: {exists}", file=sys.stderr)
    print(f"PRAGMA user_version: {user_version}", file=sys.stderr)
    print(f"MAX(version): {max_version}", file=sys.stderr)
    print(f"Backend: {cfg.backend}", file=sys.stderr)

    if cfg.backend == "sqlite" and (not has_review_status or not has_review_type):
        print(
            "\nDatabase schema is older than S4.2.\n"
            "Run:\n"
            "python -m bot.research.market_events market-event-migrate\n",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase E market shock paper research")
    parser.add_argument(
        "command",
        choices=(
            "market-event-migrate",
            "shock-paper-run",
            "observe-run",
            "observe-report",
            "activation-explain",
            "shock-event-report",
            "shock-strategy-report",
            "shock-context-report",
            "shock-lifecycle-audit",
            "shock-opportunity-audit",
            "shock-f-shadow-audit",
            "shock-f-v2-shadow-audit",
            "tradfi-shock-readiness",
            "shock-strategy-matrix-report",
            "pending-reversal-report",
            "shock-profile-report",
            "reversal-counterfactual-report",
            "shock-near-miss-report",
            "collector-path-audit",
            "market-alert-audit",
            "ai-analysis-audit",
            "ai-event-report",
            "ai-analyze-pending",
            "polymarket-paper-audit",
            "architecture-audit",
            "cli-architecture-audit",
            "instrument-discover",
            "instrument-report",
            "e2-audit",
            "index-discovery-audit",
            "historical-replay-coverage",
            "historical-candle-backfill",
            "historical-candle-coverage",
            "historical-shock-replay",
            "historical-shock-report",
            "historical-reversal-report",
            "historical-strategy-matrix",
            "historical-context-report",
            "ai-critic-replay-report",
            "market-heartbeat-preview",
            "market-daily-digest",
            "market-weekly-report",
            "market-timeline-report",
            "market-opportunity-report",
            "market-ai-comparison-report",
            "dashboard-api-serve",
            "system-validation",
            "start-all",
            "stop-all",
            "status",
            "health",
            "doctor",
            "watch",
            "self-test",
            "performance",
            "trade-import",
            "trade-list",
            "trade-report",
            "trade-similar",
            "trade-help",
            "trading-audit",
            "report",
            "telegram-status",
            "restart-telegram",
            "ai-worker-run",
            "telegram-alert-test",
            "ai-test",
            "demo-event",
            "telegram-health",
            "telegram-health-report",
            "telegram-config",
            "telegram-retry-unsent",
            "multitimeframe-report",
            "exhaustion-report",
            "exchange-context-report",
            "signal-quality-report",
            "weekly-signal-ranking",
            "trader-performance-report",
            "trader-ranking-report",
            "market-score-report",
            "liquidation-report",
            "whale-report",
            "dominance-report",
            "signal-trace-report",
            "today-summary",
            "live-dashboard",
            "yesterday-report",
            "near-miss-report",
            "detector-stats",
            "threshold-report",
            "liquidity-trend-report",
            "claude-test",
            "claude-health",
            "g2-trace",
            "g3-run",
            "g3-health",
            "g3-trace",
            "candidates",
            "candidate-stats",
            "candidate-coverage",
            "candidate-replay",
            "score-breakdown",
            "score-correlation",
            "score-recommendations",
            "market-heatmap",
            "heartbeat-trace",
            "validation-report",
            "feature-importance",
            "false-rejects",
            "false-accepts",
            "optimizer-report",
            "quant-research",
            "quant-report",
            "quant-debug",
            "research-data-audit",
            "research-lake-build",
            "market-memory",
            "signal-discovery",
            "why-not",
            "trend-status",
            "history-backfill",
            "trend-history-report",
            "experimental",
            "threshold-simulator",
            "shadow-report",
            "g40-shadow-report",
            "shadow-open",
            "shadow-trace",
            "shadow-self-test",
            "validation-open",
            "validation-report",
            "validation-force",
            "decision",
            "explain",
            "explain-decision",
            "pattern",
            "pattern-build",
            "news-update",
            "news-latest",
            "news-intel-worker",
            "news-intel-aggregate",
            "news-intel-brief",
            "news-intel-reports",
            "event-engine-run",
            "multi-source-run",
            "source-health",
            "narrative-engine-run",
            "reversal-diagnostics",
            "signal-inbox",
            "review",
            "learning-status",
            "learning-health",
            "learning-worker",
            "paper-performance",
            "paper-gate-funnel",
            "trade-regression-audit",
            "trade-postmortem",
            "market-regime",
            "decision-report",
            "feature-lab",
            "strategy-discovery",
            "alpha-discovery",
            "intelligence-report",
            "explain-drift",
            "discover-patterns",
            "morning-report",
            "pnl-killers",
            "simulate-filters",
            "long-short-analysis",
            "audit-trade-data",
            "backfill-history",
            "market-research-migrate",
            "research-stress-test",
            "trade-suggestions",
            "approve-suggestion",
            "reject-suggestion",
            "research-ai",
            "research-audit",
            "research-cost",
            "research-history",
            "research-artifacts",
            "research-compare",
            "pipeline-audit",
            "recorder-debug",
            "telegram-debug",
            "telegram-inbound-debug",
            "telegram-self-test",
            "simulate-telegram-message",
            "emit-test-signal",
            "data-source-debug",
            "volume-debug",
            "env-debug",
            "api-test",
            "provider-status",
            "sqlite-lock-debug",
            "sqlite-contention-report",
            "sqlite-lock-smoke",
            "threshold-optimizer",
            "db-info",
            "market-db-info",
            "market-db-check",
            "market-db-copy",
            "migrate-to-postgres",
            "market-db-benchmark",
            "market-db-backup",
            "market-db-restore",
        ),
    )
    parser.add_argument(
        "--universe",
        default=None,
        help="shock-paper: core|tradfi-liquid|multi-paper | observe: tradfi-observe|all-observe|crypto-observe",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Explicit venue or canonical symbols, comma-separated",
    )
    parser.add_argument(
        "--enable-tradfi",
        action="store_true",
        help="Allow PAPER_ACTIVE promotion when activation rules pass (not blind enable)",
    )
    parser.add_argument("--paper-only", action="store_true", default=True)
    parser.add_argument("--max-cycles", type=int, default=None, help="Limit poll cycles (testing)")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="narrative-engine-run: print load/skip diagnostics (implies one-shot if no --max-cycles)",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="multi-source-run: limit to one collector (rss|telegram|twitter|polymarket|macro)",
    )
    parser.add_argument(
        "--max-reviews-per-cycle",
        type=int,
        default=5,
        help="learning-worker: max Claude reviews per cycle (default 5)",
    )
    parser.add_argument("--heartbeat-sec", type=int, default=None, help="Heartbeat interval (default 60)")
    parser.add_argument("--seconds", type=int, default=30, help="Duration for collector-path-audit")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--hours", type=int, default=24, help="Hours lookback (G3.8 / paper-gate-funnel)")
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="reversal-diagnostics / signal-inbox: row limit",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=None,
        help="signal-inbox: last N rows (alias for --limit)",
    )
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="history-backfill: skip post-backfill trend/candidate rebuild",
    )
    parser.add_argument("--strategy", type=str, default=None, help="Strategy name prefix filter")
    parser.add_argument("--symbol", default=None, help="Single symbol for activation-explain")
    parser.add_argument(
        "--trade-id",
        type=int,
        default=None,
        dest="trade_id",
        help="explain-decision: paper trade id for S58 decision trace",
    )
    parser.add_argument("--run-tag", default="e4_default", help="Historical replay run tag")
    parser.add_argument("--asset-class", default=None, help="Asset class filter (CRYPTO, EQUITY, …)")
    parser.add_argument("--start", type=int, default=None, help="Backfill start unix ts")
    parser.add_argument("--end", type=int, default=None, help="Backfill end unix ts")
    parser.add_argument(
        "--suggestion-id",
        type=int,
        default=None,
        help="approve-suggestion / reject-suggestion: suggestion id",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="trade-postmortem: force run even before RCA_EVERY_N",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="trade-postmortem / market-regime: backfill missing snapshots or regimes",
    )
    parser.add_argument(
        "--backfill-last",
        type=int,
        default=None,
        dest="backfill_last",
        help="trade-postmortem / market-regime: backfill at most N rows",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="market-regime: run LLM Q&A on regime report (no auto-apply)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="research-stress-test: parallel worker count (default 100)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="strategy-discovery / alpha-discovery: show top N ranked hypotheses (default 25)",
    )
    parser.add_argument(
        "--period",
        action="append",
        choices=("lifetime", "24h", "3h", "1h"),
        default=None,
        help="intelligence-report / discover-patterns: period (repeatable). Default: all four",
    )
    parser.add_argument(
        "--last-trades",
        action="append",
        type=int,
        choices=(100, 500, 1000),
        default=None,
        dest="last_trades",
        help="discover-patterns: last-N trades universe (repeatable). Default: 100,500,1000",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=None,
        dest="min_trades",
        help=(
            "discover-patterns: lifetime min_trades override (default 50); "
            "1h/3h/24h/last_N use adaptive thresholds"
        ),
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="intelligence-report: print markdown to stdout",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="cli-architecture-audit: write docs/research/S60_1_CLI_ARCHITECTURE_AUDIT.md",
    )
    parser.add_argument(
        "--write-suggestions",
        action="store_true",
        dest="write_suggestions",
        help="market-regime / strategy-discovery / alpha-discovery: also write WAITING_APPROVAL suggestions",
    )
    parser.add_argument("--timeframe", default="1m", help="Candle timeframe for backfill")
    parser.add_argument("--event-id", type=int, default=None, help="Event id for timeline/opportunity reports")
    parser.add_argument("--port", type=int, default=None, help="Dashboard API port")
    parser.add_argument("--read-only", action="store_true", help="Skip mutating validation checks")
    parser.add_argument("--skip-load", action="store_true", help="Skip synthetic load test")
    parser.add_argument("--load-events", type=int, default=200, help="Synthetic load test event count")
    parser.add_argument("--json", action="store_true", help="JSON output for system-validation / pattern")
    parser.add_argument(
        "--examples",
        action="store_true",
        help="pattern: include up to 10 historical case examples",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="ai-test: send AI research note to Telegram",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="telegram-retry-unsent: retry all failed deliveries (default: last 100)",
    )
    parser.add_argument(
        "--force-g2",
        action="store_true",
        help="demo-event: run G2 Claude research pipeline ignoring filters",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Backup archive path for market-db-restore",
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="trader-performance-report: filter by Telegram channel name",
    )
    parser.add_argument(
        "--message",
        default=None,
        help="simulate-telegram-message: inbound text body",
    )
    parser.add_argument(
        "message_text",
        nargs="?",
        default=None,
        help="simulate-telegram-message: inbound text (positional)",
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="paper-performance: today's AI paper report",
    )
    parser.add_argument(
        "--week",
        action="store_true",
        help="paper-performance: weekly paper report",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="sqlite-lock-smoke: commands per type (default 100)",
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="doctor/self-test/watch: skip live HTTP API probes",
    )
    parser.add_argument(
        "--platform",
        action="store_true",
        help="doctor: legacy AI Trading Platform report (Telegram uses runtime by default via /doctor)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="watch: refresh interval seconds (default 5)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="watch: print one frame and exit (no full-screen loop)",
    )
    parser.add_argument(
        "--equity",
        action="store_true",
        help="performance: ASCII equity curve",
    )
    parser.add_argument(
        "--csv",
        metavar="PATH",
        default=None,
        help="performance: export completed trades CSV | trade import --source csv: input path",
    )
    parser.add_argument(
        "--side",
        default=None,
        help="trade import --source manual: LONG|SHORT",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="trade import --source manual: optional note text",
    )
    args = parser.parse_args(
        _normalize_trade_argv(argv if argv is not None else sys.argv[1:]),
    )
    explicit_symbols = _parse_symbols(args.symbols)

    if args.command == "ai-worker-run":
        from bot.research.market_events.ai_worker_runner import run_ai_worker
        run_ai_worker()
        return 0

    if args.command == "start-all":
        from bot.research.market_events.process_manager import start_all
        for line in start_all():
            print(line)
        return 0

    if args.command == "stop-all":
        from bot.research.market_events.process_manager import stop_all
        for line in stop_all():
            print(line)
        return 0

    if args.command == "status":
        from bot.research.market_events.process_manager import status_report
        print(status_report())
        return 0

    if args.command == "health":
        from bot.research.market_events.process_manager import system_health_report
        print(system_health_report())
        return 0

    if args.command == "doctor":
        if args.platform:
            from bot.research.market_events.doctor import run_platform_doctor

            print(run_platform_doctor(skip_network=bool(args.skip_network)))
        else:
            from bot.research.market_events.runtime_health import run_runtime_doctor

            print(run_runtime_doctor(skip_network=bool(args.skip_network)))
        return 0

    if args.command == "watch":
        from bot.research.market_events.runtime_health import (
            collect_watch_snapshot,
            format_watch_frame,
            run_watch,
        )

        if args.once:
            rh = collect_watch_snapshot(skip_network=bool(args.skip_network))
            print(format_watch_frame(rh))
        else:
            run_watch(interval_sec=max(1.0, float(args.interval)), skip_network=bool(args.skip_network))
        return 0

    if args.command == "self-test":
        from bot.research.market_events.runtime_health import run_self_test

        print(run_self_test(skip_network=bool(args.skip_network)))
        return 0

    if args.command == "performance":
        from bot.research.market_events.performance import run_performance_cli

        with market_events_connection() as conn:
            apply_migrations(conn)
            print(
                run_performance_cli(
                    conn,
                    equity=bool(args.equity),
                    csv_path=args.csv,
                    days=None,
                ),
            )
        return 0

    if args.command in (
        "trade-import",
        "trade-list",
        "trade-report",
        "trade-similar",
        "trade-help",
    ):
        from bot.research.market_events.trade_intelligence.cli import run_trade_cli

        action = args.command.removeprefix("trade-")
        if action == "help":
            action = ""
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(
                run_trade_cli(
                    conn,
                    action=action,
                    source=args.source,
                    csv_path=args.csv,
                    trade_id=getattr(args, "trade_id", None),
                    symbol=args.symbols,
                    side=args.side,
                    limit=max(1, int(args.limit or 50)),
                    note=args.note,
                ),
            )
            conn.commit()
        return 0

    if args.command == "trading-audit":
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            format_trading_audit_report,
        )
        print(format_trading_audit_report())
        return 0

    if args.command == "report":
        # S54 ops alias — paper performance report (includes Classic/Trailing stats).
        if _audit_s42_db_path(command="report") != 0:
            return 1
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            format_paper_performance_s42,
        )
        try:
            with market_events_readonly_connection() as conn:
                print(format_paper_performance_s42(conn))
        except Exception as exc:
            print(f"report failed: {exc}")
            return 1
        return 0

    if args.command == "telegram-status":
        from bot.research.market_events.process_manager import telegram_status_report
        print(telegram_status_report())
        return 0

    if args.command == "restart-telegram":
        from bot.research.market_events.process_manager import restart_telegram
        for line in restart_telegram():
            print(line)
        return 0

    if args.command == "telegram-alert-test":
        from bot.research.market_events.telegram_ops.cli import run_telegram_alert_test
        # FIX-3 READ ONLY: optional RO db probe for message text; no INSERT/UPDATE/migrate.
        try:
            with market_events_readonly_connection() as conn:
                conn.execute("SELECT 1")
                code, text = run_telegram_alert_test(conn)
        except Exception:
            code, text = run_telegram_alert_test(None)
        print(text)
        return code

    if args.command == "ai-test":
        from bot.research.market_events.telegram_ops.cli import run_ai_test
        with market_events_connection() as conn:
            apply_migrations(conn)
            code, text = run_ai_test(conn, send_telegram=args.send_telegram)
            conn.commit()
            print(text)
        return code

    if args.command == "demo-event":
        from bot.research.market_events.telegram_ops.cli import run_demo_event
        with market_events_connection() as conn:
            apply_migrations(conn)
            code, text = run_demo_event(conn, force_g2=args.force_g2)
            conn.commit()
            print(text)
        return code

    if args.command == "telegram-health":
        from bot.research.market_events.telegram_ops.cli import run_telegram_health
        with market_events_readonly_connection() as conn:
            print(run_telegram_health(conn))
        return 0

    if args.command == "telegram-health-report":
        from bot.research.market_events.signal_intelligence.telegram_diagnostics_s631 import (
            format_telegram_diagnostics_summary,
            run_telegram_diagnostics,
        )
        try:
            out = run_telegram_diagnostics()
            if args.json:
                slim = {k: v for k, v in out.items() if k != "delivery_health_text"}
                print(json.dumps(slim, indent=2, default=str))
            else:
                print(format_telegram_diagnostics_summary(out))
            return 0 if out.get("ok") else 1
        except Exception as exc:
            print(f"telegram-health-report failed: {exc}")
            return 1

    if args.command == "telegram-config":
        from bot.research.market_events.telegram_ops.config_report import format_telegram_config_report
        print(format_telegram_config_report())
        return 0

    if args.command == "telegram-retry-unsent":
        from bot.research.market_events.telegram_ops.retry import retry_failed_deliveries
        with market_events_connection() as conn:
            apply_migrations(conn)
            lines = retry_failed_deliveries(conn, retry_all=args.all)
            conn.commit()
            for line in lines:
                print(line)
            failed = sum(1 for line in lines if line.startswith("✗ id=") and "failed" in line)
            sent = sum(1 for line in lines if line.startswith("✓ id="))
            return 1 if failed and not sent else 0

    if args.command in (
        "multitimeframe-report",
        "exhaustion-report",
        "exchange-context-report",
        "signal-quality-report",
        "weekly-signal-ranking",
        "trader-performance-report",
        "trader-ranking-report",
    ):
        from bot.research.market_events.signal_intelligence.reports import (
            exchange_context_report,
            exhaustion_report,
            multitimeframe_report,
            signal_quality_report,
            weekly_ranking_report,
        )
        from bot.research.market_events.signal_intelligence.trader_performance_f6 import (
            trader_performance_report,
            trader_ranking_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            if args.command == "multitimeframe-report":
                print(multitimeframe_report(conn, days=args.days))
            elif args.command == "exhaustion-report":
                print(exhaustion_report(conn, days=args.days))
            elif args.command == "exchange-context-report":
                print(exchange_context_report(conn, days=args.days))
            elif args.command == "signal-quality-report":
                print(signal_quality_report(conn, days=args.days))
            elif args.command == "weekly-signal-ranking":
                print(weekly_ranking_report(conn))
                conn.commit()
            elif args.command == "trader-performance-report":
                print(trader_performance_report(conn, channel=args.channel))
            elif args.command == "trader-ranking-report":
                print(trader_ranking_report(conn))
        return 0

    if args.command in (
        "market-score-report",
        "liquidation-report",
        "whale-report",
        "dominance-report",
        "signal-trace-report",
        "today-summary",
    ):
        from bot.research.market_events.signal_intelligence.ops_reports_f71 import (
            dominance_report,
            liquidation_report,
            market_score_report,
            signal_trace_report,
            today_summary_report,
            whale_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            if args.command == "market-score-report":
                print(market_score_report(conn, days=args.days))
            elif args.command == "liquidation-report":
                print(liquidation_report(conn, days=args.days))
            elif args.command == "whale-report":
                print(whale_report(conn, days=args.days))
            elif args.command == "dominance-report":
                print(dominance_report(conn, days=args.days))
            elif args.command == "signal-trace-report":
                print(signal_trace_report(conn, days=max(1, args.days)))
            elif args.command == "today-summary":
                print(today_summary_report(conn))
        return 0

    if args.command == "live-dashboard":
        from bot.research.market_events.signal_intelligence.live_dashboard_f71 import run_live_dashboard
        interval = float(args.seconds) if args.seconds else 5.0
        run_live_dashboard(interval_sec=interval)
        return 0

    if args.command == "yesterday-report":
        from bot.research.market_events.signal_intelligence.yesterday_report_f72 import yesterday_report
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(yesterday_report(conn))
        return 0

    if args.command in ("near-miss-report", "detector-stats", "threshold-report"):
        from bot.research.market_events.signal_intelligence.reports_f73 import (
            detector_stats_report,
            near_miss_report,
            threshold_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            if args.command == "near-miss-report":
                print(near_miss_report(conn, days=max(1, args.days)))
            elif args.command == "detector-stats":
                print(detector_stats_report(conn, days=args.days if args.days else None))
            else:
                print(threshold_report(conn, days=max(1, args.days)))
        return 0

    if args.command == "liquidity-trend-report":
        from bot.research.market_events.signal_intelligence.liquidity_trend_g1 import (
            liquidity_trend_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(liquidity_trend_report(conn, days=max(1, args.days)))
        return 0

    if args.command == "claude-test":
        from bot.research.market_events.signal_intelligence.claude_ops_g2 import run_claude_test
        code, text = run_claude_test()
        print(text)
        return code

    if args.command == "claude-health":
        from bot.research.market_events.signal_intelligence.claude_ops_g2 import format_claude_health_report
        # FIX-G2.1: pure read-only — no apply_migrations / INSERT from health path.
        with market_events_readonly_connection() as conn:
            print(format_claude_health_report(conn))
        return 0

    if args.command == "g2-trace":
        from bot.research.market_events.signal_intelligence.research_g2 import format_g2_trace
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_g2_trace(conn, args.event_id))
        return 0

    if args.command == "g3-run":
        from bot.research.market_events.signal_intelligence.runner_g3 import run_g3_live
        stats = run_g3_live(max_cycles=args.max_cycles, interval_sec=args.heartbeat_sec)
        print(
            f"G3 cycles={stats.cycles} snapshots={stats.snapshots} trends={stats.trends} "
            f"candidates={stats.candidates} signals={stats.signals} followups={stats.followups} "
            f"errors={stats.errors}",
        )
        return 1 if stats.errors and not stats.snapshots else 0

    if args.command == "g3-health":
        from bot.research.market_events.signal_intelligence.health_g3 import format_g3_health_report
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_g3_health_report(conn))
        return 0

    if args.command == "g3-trace":
        from bot.research.market_events.signal_intelligence.signal_generator_g3 import format_g3_trace
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_g3_trace(conn, args.event_id))
        return 0

    if args.command == "candidates":
        from bot.research.market_events.signal_intelligence.candidate_g31 import format_candidates_report
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_candidates_report(conn, limit=20))
        return 0

    if args.command == "candidate-stats":
        from bot.research.market_events.signal_intelligence.candidate_g31 import format_candidate_stats
        with market_events_connection() as conn:
            apply_migrations(conn)
            hours = max(1, args.days * 24 if args.days else 24)
            print(format_candidate_stats(conn, hours=hours))
        return 0

    if args.command == "candidate-coverage":
        from bot.research.market_events.signal_intelligence.trend_coverage_g33 import (
            format_candidate_coverage_report,
        )
        with market_events_connection() as conn:
            symbol = getattr(args, "symbol", None)
            print(format_candidate_coverage_report(conn, symbol=symbol))
        return

    if args.command == "candidate-replay":
        from bot.research.market_events.signal_intelligence.replay_g32 import format_candidate_replay_report
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_candidate_replay_report(conn, limit=20))
        return 0

    if args.command == "score-breakdown":
        from bot.research.market_events.signal_intelligence.score_breakdown_g34 import (
            format_score_breakdown_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_score_breakdown_report(conn, symbol=args.symbol))
        return 0

    if args.command == "score-correlation":
        from bot.research.market_events.signal_intelligence.score_breakdown_g34 import (
            format_score_correlation_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_score_correlation_report(conn))
        return 0

    if args.command == "score-recommendations":
        from bot.research.market_events.signal_intelligence.score_breakdown_g34 import (
            format_score_recommendations_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_score_recommendations_report(conn))
        return 0

    if args.command == "market-heatmap":
        from bot.research.market_events.signal_intelligence.score_breakdown_g34 import (
            format_market_heatmap,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_market_heatmap(conn, limit=8))
        return 0

    if args.command == "heartbeat-trace":
        from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
            format_heartbeat_trace,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_heartbeat_trace(conn))
        return 0

    if args.command == "validation-report":
        from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
            format_validation_report_g4,
            run_validation_cycle_g4,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            run_validation_cycle_g4(conn, days=max(1, args.days))
            conn.commit()
            print(format_validation_report_g4(conn, days=max(1, args.days)))
        return 0

    if args.command == "feature-importance":
        from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
            format_feature_importance_report_g4,
            run_validation_cycle_g4,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            run_validation_cycle_g4(conn, days=max(1, args.days))
            conn.commit()
            print(format_feature_importance_report_g4(conn))
        return 0

    if args.command == "false-rejects":
        from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
            format_false_rejects_report_g4,
            run_validation_cycle_g4,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            run_validation_cycle_g4(conn, days=max(1, args.days))
            conn.commit()
            print(format_false_rejects_report_g4(conn))
        return 0

    if args.command == "false-accepts":
        from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
            format_false_accepts_report_g4,
            run_validation_cycle_g4,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            run_validation_cycle_g4(conn, days=max(1, args.days))
            conn.commit()
            print(format_false_accepts_report_g4(conn))
        return 0

    if args.command == "optimizer-report":
        from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
            run_validation_cycle_g4,
        )
        from bot.research.market_events.signal_intelligence.threshold_optimizer_g42 import (
            format_optimizer_report_g42,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            run_validation_cycle_g4(conn, days=max(1, args.days))
            conn.commit()
            print(format_optimizer_report_g42(conn, days=max(1, args.days)))
        return 0

    if args.command == "quant-research":
        from bot.research.market_events.signal_intelligence.quant_research_g50 import run_quant_research_g50
        with market_events_connection() as conn:
            apply_migrations(conn)
            result = run_quant_research_g50(conn, force=True, days=max(1, args.days))
            conn.commit()
            print(json.dumps(result.get("report") or result, indent=2, ensure_ascii=False, default=str))
        return 0

    if args.command == "quant-report":
        from bot.research.market_events.signal_intelligence.quant_research_g50 import format_quant_report_cli_g50
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_quant_report_cli_g50(conn))
        return 0

    if args.command == "quant-debug":
        from bot.research.market_events.signal_intelligence.quant_research_g50 import format_quant_debug_g50
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_quant_debug_g50(conn))
        return 0

    if args.command == "research-data-audit":
        from bot.research.market_events.signal_intelligence.research_dataset_g51 import (
            format_research_data_audit_g51,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_research_data_audit_g51(conn))
        return 0

    if args.command == "research-lake-build":
        from bot.research.market_events.signal_intelligence.research_dataset_g51 import (
            build_research_lake_g51,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            result = build_research_lake_g51(conn, days=max(1, args.days))
            conn.commit()
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0

    if args.command == "market-memory":
        from bot.research.market_events.signal_intelligence.market_memory_g36 import (
            format_market_memory_cli_g36,
        )
        symbol = (args.symbol or "BTC").upper()
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_market_memory_cli_g36(conn, symbol))
        return 0

    if args.command == "signal-discovery":
        from bot.research.market_events.signal_intelligence.signal_discovery_g37 import (
            format_signal_discovery_report_g37,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_signal_discovery_report_g37(conn, hours=max(24, args.days * 24)))
        return 0

    if args.command == "why-not":
        from bot.research.market_events.signal_intelligence.signal_discovery_g37 import (
            format_pipeline_trace_g37,
            format_why_not_g37,
        )
        symbol = (args.symbol or "BTC").upper()
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_why_not_g37(conn, symbol))
            print("")
            print(format_pipeline_trace_g37(conn, symbol))
        return 0

    if args.command == "trend-status":
        from bot.research.market_events.signal_intelligence.trend_history_g38 import (
            format_candle_source_report_g38,
            format_trend_status_g38,
        )
        symbol = getattr(args, "symbol", None)
        hours = max(1, args.hours or 24)
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_trend_status_g38(conn, symbol=symbol, hours=hours))
            print("")
            print("---")
            print("")
            print(format_candle_source_report_g38(conn, hours=hours))
        return 0

    if args.command == "history-backfill":
        from bot.research.market_events.signal_intelligence.trend_history_g38 import (
            format_history_backfill_summary_g38,
            run_history_backfill_g38,
        )
        hours = max(1, args.hours or 24)
        syms = explicit_symbols
        with market_events_connection() as conn:
            apply_migrations(conn)
            stats = run_history_backfill_g38(
                conn,
                symbols=syms,
                hours=hours,
                run_pipeline=not args.skip_pipeline,
            )
            conn.commit()
            print("")
            print(format_history_backfill_summary_g38(stats))
        return 0

    if args.command == "trend-history-report":
        from bot.research.market_events.signal_intelligence.trend_history_g38 import (
            format_trend_history_report_g38,
        )
        hours = max(1, args.hours or 24)
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_trend_history_report_g38(conn, hours=hours))
        return 0

    if args.command == "experimental":
        from bot.research.market_events.signal_intelligence.experimental_g39 import (
            format_experimental_report_g39,
            format_replay_wr_comparison_g39,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_experimental_report_g39(conn))
            print("")
            print("---")
            print("")
            print(format_replay_wr_comparison_g39(conn, days=max(1, args.days)))
        return 0

    if args.command == "threshold-simulator":
        from bot.research.market_events.signal_intelligence.experimental_g39 import (
            format_threshold_simulator_g39,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_threshold_simulator_g39(conn, days=max(1, args.days)))
        return 0

    if args.command == "shadow-report":
        from bot.research.market_events.adaptive_shock_shadow import shadow_profile_report
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(shadow_profile_report(conn, days=max(1, args.days)))
        return 0

    if args.command == "g40-shadow-report":
        from bot.research.market_events.signal_intelligence.shadow_g40 import (
            format_shadow_report_g40,
            lane_comparison_stats_g40,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_shadow_report_g40(conn, days=max(1, args.days)))
            cmp = lane_comparison_stats_g40(conn, days=max(1, args.days))
            print("")
            print("Lane Comparison")
            print(f"Production: {cmp['production']}")
            print(f"Shadow: {cmp['shadow']}")
            print(f"Combined: {cmp['combined']}")
        return 0

    if args.command == "shadow-open":
        from bot.research.market_events.signal_intelligence.shadow_g40 import format_shadow_open_g40
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_shadow_open_g40(conn))
        return 0

    if args.command == "shadow-trace":
        from bot.research.market_events.signal_intelligence.shadow_pipeline_g401 import (
            format_shadow_trace_g401,
        )
        symbol = args.symbols.split(",")[0].strip().upper() if args.symbols else None
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_shadow_trace_g401(conn, symbol=symbol))
        return 0

    if args.command == "shadow-self-test":
        from bot.research.market_events.signal_intelligence.shadow_pipeline_g401 import (
            format_shadow_self_test_g401,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_shadow_self_test_g401(conn))
        return 0

    if args.command == "validation-open":
        from bot.research.market_events.signal_intelligence.validation_signal_s11 import (
            format_validation_open_s11,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_validation_open_s11(conn))
        return 0

    if args.command == "validation-report":
        from bot.research.market_events.signal_intelligence.validation_signal_s11 import (
            format_validation_report_s11,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_validation_report_s11(conn, days=max(1, args.days)))
        return 0

    if args.command == "validation-force":
        from bot.research.market_events.signal_intelligence.validation_signal_s11 import (
            force_validation_signal_s11,
            format_validation_open_s11,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            sig = force_validation_signal_s11(conn)
            conn.commit()
            if sig:
                print(f"VALIDATION SIGNAL created id={sig.signal_id} symbol={sig.symbol}")
                print("")
                print(sig.telegram_rendered)
            else:
                print("VALIDATION SIGNAL not created (no candidates)")
            print("")
            print(format_validation_open_s11(conn))
        return 0

    if args.command == "decision":
        from bot.research.market_events.signal_intelligence.decision_engine_s20 import (
            run_decision_engine_s20,
        )
        symbol = (args.symbol or "BTC").upper().replace("USDT", "")
        if getattr(args, "message_text", None) and not args.symbol:
            symbol = str(args.message_text).upper().replace("USDT", "")
        # S2.2: READ ONLY — use readonly connection, never persist.
        with market_events_readonly_connection() as conn:
            result = run_decision_engine_s20(conn, symbol, persist=False)
            print(result["telegram"])
        return 0

    if args.command in ("explain-decision", "explain"):
        # S58: --trade-id → full decision trace. Else S22 symbol explain (unchanged).
        if getattr(args, "trade_id", None) is not None:
            from bot.research.market_events.signal_intelligence.decision_trace_s58 import (
                format_explain_trade,
                get_decision,
            )
            from bot.research.market_events.signal_intelligence.research_repository_s60 import (
                apply_research_migrations,
                research_connection,
            )
            with research_connection() as conn:
                apply_research_migrations(conn)
                conn.commit()
                if args.json:
                    print(json.dumps(get_decision(conn, int(args.trade_id)), indent=2, default=str))
                else:
                    print(format_explain_trade(conn, int(args.trade_id)))
            return 0
        from bot.research.market_events.signal_intelligence.explain_decision_s22 import (
            format_explain_decision_s22,
        )
        symbol = (args.symbol or "BTC").upper().replace("USDT", "")
        if getattr(args, "message_text", None) and not args.symbol:
            symbol = str(args.message_text).upper().replace("USDT", "")
        with market_events_readonly_connection() as conn:
            print(format_explain_decision_s22(conn, symbol))
        return 0

    if args.command == "decision-report":
        from bot.research.market_events.signal_intelligence.decision_trace_s58 import (
            compute_decision_report,
            format_decision_report,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            apply_research_migrations,
            research_connection,
        )
        try:
            with research_connection() as conn:
                apply_research_migrations(conn)
                conn.commit()
                if args.json:
                    print(json.dumps(compute_decision_report(conn), indent=2, default=str))
                else:
                    print(format_decision_report(conn))
            return 0
        except Exception as exc:
            print(f"decision-report failed: {exc}")
            return 1

    if args.command == "feature-lab":
        from bot.research.market_events.db import retry_on_db_locked
        from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
            format_feature_lab_report,
            run_feature_lab,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            apply_research_migrations,
            research_connection,
        )
        try:
            def _run_lab() -> int:
                with research_connection() as conn:
                    apply_research_migrations(conn)
                    if args.force or args.llm:
                        out = run_feature_lab(conn, with_llm=bool(args.llm))
                        conn.commit()
                        if args.json:
                            print(json.dumps(out, indent=2, default=str))
                        else:
                            print(format_feature_lab_report(conn, run_id=out.get("run_id")))
                        return 0
                    conn.commit()
                    if args.json:
                        from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
                            latest_lab_rows,
                        )
                        print(json.dumps({
                            "rows": latest_lab_rows(conn),
                            "report": format_feature_lab_report(conn),
                        }, indent=2, default=str))
                    else:
                        print(format_feature_lab_report(conn))
                    return 0

            return int(retry_on_db_locked(_run_lab))
        except Exception as exc:
            print(f"feature-lab failed: {exc}")
            return 1

    if args.command == "strategy-discovery":
        from bot.research.market_events.db import retry_on_db_locked
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            apply_research_migrations,
            research_connection,
        )
        from bot.research.market_events.signal_intelligence.strategy_discovery_s61 import (
            format_strategy_discovery_report,
            latest_discovery_rows,
            run_strategy_discovery,
        )
        try:
            def _run_discovery() -> int:
                with research_connection() as conn:
                    apply_research_migrations(conn)
                    top_n = getattr(args, "top", None)
                    if args.force:
                        out = run_strategy_discovery(
                            conn,
                            top_n=top_n,
                            write_suggestions=bool(
                                getattr(args, "write_suggestions", False),
                            ),
                        )
                        conn.commit()
                        if args.json:
                            print(json.dumps(out, indent=2, default=str))
                        else:
                            print(format_strategy_discovery_report(conn, run_id=out.get("run_id")))
                        return 0
                    conn.commit()
                    if args.json:
                        print(json.dumps({
                            "rows": latest_discovery_rows(conn, limit=int(top_n or 50)),
                            "report": format_strategy_discovery_report(conn, top_n=top_n),
                        }, indent=2, default=str))
                    else:
                        print(format_strategy_discovery_report(conn, top_n=top_n))
                    return 0

            return int(retry_on_db_locked(_run_discovery))
        except Exception as exc:
            print(f"strategy-discovery failed: {exc}")
            return 1

    if args.command == "alpha-discovery":
        from bot.research.market_events.db import retry_on_db_locked
        from bot.research.market_events.signal_intelligence.alpha_discovery_s62 import (
            format_alpha_discovery_report,
            latest_alpha_rows,
            run_alpha_discovery,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            apply_research_migrations,
            research_connection,
        )
        try:
            def _run_alpha() -> int:
                with research_connection() as conn:
                    apply_research_migrations(conn)
                    top_n = getattr(args, "top", None)
                    if args.force:
                        out = run_alpha_discovery(
                            conn,
                            top_n=top_n,
                            write_suggestions=bool(
                                getattr(args, "write_suggestions", False),
                            ),
                        )
                        conn.commit()
                        if args.json:
                            print(json.dumps(out, indent=2, default=str))
                        else:
                            print(format_alpha_discovery_report(conn, run_id=out.get("run_id")))
                        return 0
                    conn.commit()
                    if args.json:
                        print(json.dumps({
                            "rows": latest_alpha_rows(conn, limit=int(top_n or 50)),
                            "report": format_alpha_discovery_report(conn),
                        }, indent=2, default=str))
                    else:
                        print(format_alpha_discovery_report(conn))
                    return 0

            return int(retry_on_db_locked(_run_alpha))
        except Exception as exc:
            print(f"alpha-discovery failed: {exc}")
            return 1

    if args.command == "intelligence-report":
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )
        from bot.research.market_events.signal_intelligence.trading_intelligence_report_s621 import (
            format_intelligence_markdown,
            run_intelligence_report,
        )
        periods = getattr(args, "period", None) or None
        try:
            # Research DB only — no live connection, no migrations, no table writes.
            with research_connection() as conn:
                out = run_intelligence_report(conn, periods=periods)
            paths = out.get("export_paths") or {}
            if args.json:
                print(json.dumps(out, indent=2, default=str))
            elif getattr(args, "markdown", False):
                print(format_intelligence_markdown(out))
            else:
                print("S62.1 Trading Intelligence Report")
                print(f"  trades_loaded={out.get('n_trades_loaded')}  "
                      f"elapsed={out.get('elapsed_sec')}s  periods={out.get('periods')}")
                print(f"  regime={out.get('current_market_regime')}")
                drift = (out.get("performance_drift") or {}).get("flags") or []
                if drift:
                    print("  drift_flags:")
                    for f in drift[:5]:
                        print(f"    - {f}")
                print("  exports:")
                for k, p in paths.items():
                    print(f"    {k}: {p}")
            return 0
        except Exception as exc:
            print(f"intelligence-report failed: {exc}")
            return 1

    if args.command == "explain-drift":
        from bot.research.market_events.signal_intelligence.drift_analyzer_s622 import (
            format_drift_summary,
            run_drift_analyzer,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )
        try:
            with research_connection() as conn:
                out = run_drift_analyzer(conn)
            if args.json:
                # Drop bulky category list for CLI json unless full report file is enough
                slim = {k: v for k, v in out.items() if k != "categories"}
                slim["n_categories"] = len(out.get("categories") or [])
                slim["export_paths"] = out.get("export_paths")
                print(json.dumps(slim, indent=2, default=str))
            else:
                print(format_drift_summary(out))
            return 0 if out.get("ok") else 1
        except Exception as exc:
            print(f"explain-drift failed: {exc}")
            return 1

    if args.command == "discover-patterns":
        from bot.research.market_events.signal_intelligence.pattern_discovery_s623 import (
            DEFAULT_MIN_TRADES,
            format_discovery_summary,
            run_pattern_discovery,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )
        try:
            with research_connection() as conn:
                out = run_pattern_discovery(
                    conn,
                    periods=getattr(args, "period", None) or None,
                    last_trades=getattr(args, "last_trades", None),
                    min_trades=int(getattr(args, "min_trades", None) or DEFAULT_MIN_TRADES),
                )
            if args.json:
                slim = {
                    k: v for k, v in out.items()
                    if k not in ("patterns",)
                }
                slim["n_patterns"] = len(out.get("patterns") or [])
                print(json.dumps(slim, indent=2, default=str))
            else:
                print(format_discovery_summary(out))
            return 0 if out.get("ok") else 1
        except Exception as exc:
            print(f"discover-patterns failed: {exc}")
            return 1

    if args.command == "morning-report":
        from bot.research.market_events.signal_intelligence.morning_report_s63 import (
            format_morning_summary,
            run_morning_report,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )
        try:
            with research_connection() as conn:
                out = run_morning_report(conn)
            if args.json:
                slim = dict(out)
                # keep actionable payload; patterns lists already capped
                print(json.dumps(slim, indent=2, default=str))
            else:
                print(format_morning_summary(out))
            return 0 if out.get("ok") else 1
        except Exception as exc:
            print(f"morning-report failed: {exc}")
            return 1

    if args.command == "pnl-killers":
        from bot.research.market_events.signal_intelligence.pnl_killers_s64 import (
            format_pnl_killers_summary,
            run_pnl_killers,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )
        try:
            with research_connection() as conn:
                out = run_pnl_killers(conn)
            if args.json:
                print(json.dumps(out, indent=2, default=str))
            else:
                print(format_pnl_killers_summary(out))
            return 0 if out.get("ok") else 1
        except Exception as exc:
            print(f"pnl-killers failed: {exc}")
            return 1

    if args.command == "simulate-filters":
        from bot.research.market_events.signal_intelligence.filter_simulator_s65 import (
            format_filter_simulator_summary,
            run_filter_simulator,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )
        try:
            with research_connection() as conn:
                out = run_filter_simulator(conn)
            if args.json:
                print(json.dumps(out, indent=2, default=str))
            else:
                print(format_filter_simulator_summary(out))
            return 0 if out.get("ok") else 1
        except Exception as exc:
            print(f"simulate-filters failed: {exc}")
            return 1

    if args.command == "long-short-analysis":
        from bot.research.market_events.signal_intelligence.long_short_analysis_s641 import (
            format_long_short_summary,
            run_long_short_analysis,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )
        try:
            with research_connection() as conn:
                out = run_long_short_analysis(conn)
            if args.json:
                print(json.dumps(out, indent=2, default=str))
            else:
                print(format_long_short_summary(out))
            return 0 if out.get("ok") else 1
        except Exception as exc:
            print(f"long-short-analysis failed: {exc}")
            return 1

    if args.command == "audit-trade-data":
        from bot.research.market_events.signal_intelligence.trade_data_audit_s66 import (
            format_data_quality_summary,
            run_trade_data_audit,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            research_connection,
        )
        try:
            with research_connection() as conn:
                out = run_trade_data_audit(conn)
            if args.json:
                print(json.dumps(out, indent=2, default=str))
            else:
                print(format_data_quality_summary(out))
            return 0 if out.get("ok") else 1
        except Exception as exc:
            print(f"audit-trade-data failed: {exc}")
            return 1

    if args.command == "backfill-history":
        from bot.research.market_events.signal_intelligence.history_backfill_s621 import (
            format_history_backfill_report,
            run_history_backfill,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            apply_research_migrations,
            research_connection,
        )
        try:
            with research_connection() as conn:
                apply_research_migrations(conn)
                result = run_history_backfill(conn)
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(format_history_backfill_report(result))
            return 0 if result.get("ok") else 1
        except Exception as exc:
            print(f"backfill-history failed: {exc}")
            return 1

    if args.command == "pattern":
        from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
            format_pattern_report_s31,
            run_pattern_agent_s31,
        )
        # `pattern BTC` via --symbol or positional leftovers: prefer --symbol
        symbol = (args.symbol or "BTC").upper().replace("USDT", "")
        # Allow: python -m ... pattern BTC  (message_text / leftover)
        if getattr(args, "message_text", None) and not args.symbol:
            symbol = str(args.message_text).upper().replace("USDT", "")
        tf = getattr(args, "timeframe", None) or "60m"
        # Global --timeframe defaults to 1m (backfill); Pattern Agent defaults to 60m
        # unless the user explicitly passed --timeframe on the CLI.
        argv_list = argv if argv is not None else sys.argv[1:]
        if "--timeframe" not in argv_list:
            tf = "60m"
        with market_events_readonly_connection() as conn:
            result = run_pattern_agent_s31(conn, symbol=symbol, timeframe=tf)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            else:
                print(format_pattern_report_s31(result, show_examples=bool(args.examples)))
        return 0

    if args.command == "pattern-build":
        from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
            build_pattern_index_s31,
            format_pattern_build_report_s31,
        )
        with market_events_readonly_connection() as conn:
            index = build_pattern_index_s31(conn)
            print(format_pattern_build_report_s31(index))
        return 0

    if args.command == "news-update":
        from bot.research.market_events.signal_intelligence.news_collector_n11 import (
            format_news_update_report_n11,
            run_news_update_n11,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            result = run_news_update_n11(conn)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            else:
                print(format_news_update_report_n11(result))
        return 0

    if args.command == "news-latest":
        from bot.research.market_events.signal_intelligence.news_collector_n11 import (
            fetch_latest_news_n11,
            format_news_latest_n11,
        )
        limit = 10
        argv_list = argv if argv is not None else sys.argv[1:]
        if "--limit" in argv_list:
            limit = max(1, int(args.limit))
        if getattr(args, "message_text", None):
            try:
                limit = max(1, int(args.message_text))
            except (TypeError, ValueError):
                pass
        with market_events_readonly_connection() as conn:
            rows = fetch_latest_news_n11(conn, limit=limit)
            if args.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
            else:
                print(format_news_latest_n11(rows))
        return 0

    if args.command == "news-intel-worker":
        from bot.research.market_events.signal_intelligence.news_intelligence.worker import (
            _configure_logging,
            run_news_intelligence_worker_s41,
        )
        _configure_logging()
        stats = run_news_intelligence_worker_s41(
            max_cycles=args.max_cycles,
        )
        print(json.dumps(stats, indent=2) if args.json else stats)
        return 1 if stats.get("errors") and not stats.get("aggregations") else 0

    if args.command == "news-intel-aggregate":
        from bot.research.market_events.signal_intelligence.news_intelligence.aggregator import (
            run_news_aggregation_cycle_s41,
        )
        from bot.research.market_events.signal_intelligence.news_intelligence.worker import (
            _configure_logging,
        )
        _configure_logging()
        with market_events_connection() as conn:
            apply_migrations(conn)
        result = run_news_aggregation_cycle_s41()
        print(json.dumps(result, indent=2, default=str) if args.json else result)
        return 0

    if args.command == "news-intel-brief":
        from bot.research.market_events.signal_intelligence.news_intelligence.briefs import (
            run_global_brief_cycle_s41,
        )
        from bot.research.market_events.signal_intelligence.news_intelligence.reports import (
            write_period_reports_s41,
        )
        from bot.research.market_events.signal_intelligence.news_intelligence.worker import (
            _configure_logging,
        )
        _configure_logging()
        with market_events_connection() as conn:
            apply_migrations(conn)
        brief = run_global_brief_cycle_s41()
        reports = write_period_reports_s41()
        out = {"brief": brief, "reports": reports}
        print(json.dumps(out, indent=2, default=str) if args.json else out)
        return 0

    if args.command == "news-intel-reports":
        from bot.research.market_events.signal_intelligence.news_intelligence.reports import (
            write_period_reports_s41,
        )
        result = write_period_reports_s41()
        print(json.dumps(result, indent=2, default=str) if args.json else result)
        return 0

    if args.command == "event-engine-run":
        from bot.research.market_events.signal_intelligence.event_intelligence.worker import (
            _configure_logging,
            run_event_intelligence_worker_s43,
        )
        _configure_logging()
        if args.max_cycles is not None or bool(getattr(args, "debug", False)):
            from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
                run_event_engine_cycle_s43,
            )
            with market_events_connection() as conn:
                apply_migrations(conn)
            last = None
            cycles = max(1, int(args.max_cycles or 1))
            for _ in range(cycles):
                last = run_event_engine_cycle_s43()
                if getattr(args, "debug", False):
                    print(
                        f"events created={last.get('created')} "
                        f"merged={last.get('merged')} "
                        f"duplicates_removed={last.get('duplicates_removed')} "
                        f"clusters={last.get('clusters')} "
                        f"articles={last.get('articles')} "
                        f"ms={last.get('elapsed_ms')}",
                        flush=True,
                    )
            print(json.dumps(last, indent=2, default=str) if args.json else last)
            return 0
        stats = run_event_intelligence_worker_s43(max_cycles=None)
        print(json.dumps(stats, indent=2) if args.json else stats)
        return 1 if stats.get("errors") and not stats.get("runs") else 0

    if args.command == "multi-source-run":
        from bot.research.market_events.signal_intelligence.multi_source.worker import (
            _configure_logging,
            run_multi_source_worker_s44,
        )
        _configure_logging()
        with market_events_connection() as conn:
            apply_migrations(conn)
        if args.max_cycles is not None or bool(getattr(args, "debug", False)):
            from bot.research.market_events.signal_intelligence.multi_source.orchestrator import (
                run_multi_source_cycle_s44,
            )
            only = None
            if getattr(args, "source", None):
                only = [str(args.source).strip().lower()]
            last = None
            cycles = max(1, int(args.max_cycles or 1))
            for _ in range(cycles):
                last = run_multi_source_cycle_s44(only=only, run_events=True)
            print(json.dumps(last, indent=2, default=str) if args.json else last)
            return 0
        stats = run_multi_source_worker_s44(max_cycles=None)
        print(json.dumps(stats, indent=2) if args.json else stats)
        return 1 if stats.get("errors") and not stats.get("runs") else 0

    if args.command == "source-health":
        from bot.research.market_events.signal_intelligence.multi_source.health import (
            fetch_source_health,
            format_source_health_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
        rows = fetch_source_health()
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
        else:
            print(format_source_health_report(rows))
        return 0

    if args.command == "narrative-engine-run":
        from bot.research.market_events.signal_intelligence.narrative_engine.worker import (
            _configure_logging,
            run_narrative_engine_worker_s42,
        )
        _configure_logging()
        debug = bool(getattr(args, "debug", False))
        # One-shot if --max-cycles or --debug; otherwise long-running worker.
        if args.max_cycles is not None or debug:
            from bot.research.market_events.signal_intelligence.narrative_engine.engine import (
                run_narrative_engine_cycle_s42,
            )
            with market_events_connection() as conn:
                apply_migrations(conn)
            last = None
            cycles = max(1, int(args.max_cycles or 1))
            for _ in range(cycles):
                last = run_narrative_engine_cycle_s42(debug=debug)
            if not debug or args.json:
                print(json.dumps(last, indent=2, default=str) if args.json else last)
            return 0
        stats = run_narrative_engine_worker_s42(max_cycles=None)
        print(json.dumps(stats, indent=2) if args.json else stats)
        return 1 if stats.get("errors") and not stats.get("runs") else 0

    if args.command == "signal-inbox":
        from bot.research.market_events.signal_intelligence.signal_inbox_s23 import (
            format_signal_inbox_cli_s23,
            process_telegram_signal_inbox_s23,
        )
        limit = max(1, int(getattr(args, "limit", 20) or 20))
        # Allow --last via limit (CLI alias)
        if getattr(args, "last", None):
            limit = max(1, int(args.last))
        symbol = (args.symbol or None)
        if symbol:
            symbol = symbol.upper().replace("USDT", "")
        # Optional: ingest a raw message for testing
        if getattr(args, "message", None) or getattr(args, "message_text", None):
            raw = args.message or args.message_text
            reply, inbox_id = process_telegram_signal_inbox_s23(
                raw_text=raw,
                chat_id=0,
                telegram_user="cli",
            )
            print(reply)
            print("")
            print(f"inbox_id={inbox_id}")
            print("")
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_signal_inbox_cli_s23(conn, limit=limit, symbol=symbol))
        return 0

    if args.command == "learning-status":
        if _audit_s42_db_path(command="learning-status") != 0:
            return 1
        from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
            learning_status_s40,
        )
        try:
            print(learning_status_s40())
        except sqlite3.OperationalError as exc:
            if "review_status" in str(exc) or "review_type" in str(exc):
                print(
                    "Database schema is older than S4.2.\n"
                    "Run:\n"
                    "python -m bot.research.market_events market-event-migrate",
                    file=sys.stderr,
                )
                return 1
            raise
        return 0

    if args.command == "learning-health":
        if _audit_s42_db_path(command="learning-health") != 0:
            return 1
        from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
            learning_health_s40,
        )
        try:
            print(learning_health_s40())
        except sqlite3.OperationalError as exc:
            if "review_status" in str(exc) or "review_type" in str(exc):
                print(
                    "Database schema is older than S4.2.\n"
                    "Run:\n"
                    "python -m bot.research.market_events market-event-migrate",
                    file=sys.stderr,
                )
                return 1
            raise
        return 0

    if args.command == "research-ai":
        from bot.research.market_events.signal_intelligence.claude_channel_s50 import telegram_claude_session
        from bot.research.market_events.signal_intelligence.research_terminal_s50 import run_ai_s50_cli
        sym = (getattr(args, "symbol", None) or "BTC").upper().replace("USDT", "")
        with telegram_claude_session():
            print(run_ai_s50_cli(symbol=sym))
        return 0

    if args.command == "research-audit":
        from bot.research.market_events.signal_intelligence.claude_channel_s50 import telegram_claude_session
        from bot.research.market_events.signal_intelligence.audit_engine_s51 import run_audit_s51_cli
        # Allow: research-audit --symbol BTC | research-audit BTC | research-audit system
        raw = (
            getattr(args, "symbol", None)
            or getattr(args, "message_text", None)
            or "BTC"
        )
        raw = str(raw).strip()
        system = raw.lower() in {"system", "sys", "all"}
        with telegram_claude_session():
            print(run_audit_s51_cli(symbol=None if system else raw, system=system))
        return 0

    if args.command == "research-compare":
        from bot.research.market_events.signal_intelligence.claude_channel_s50 import telegram_claude_session
        from bot.research.market_events.signal_intelligence.research_terminal_s50 import run_compare_s50_cli
        sym = (getattr(args, "symbol", None) or "BTC").upper().replace("USDT", "")
        with telegram_claude_session():
            print(run_compare_s50_cli(symbol=sym))
        return 0

    if args.command == "research-cost":
        from bot.research.market_events.signal_intelligence.research_terminal_s50 import format_cost_s50_cli
        print(format_cost_s50_cli())
        return 0

    if args.command == "research-history":
        from bot.research.market_events.signal_intelligence.research_terminal_s50 import format_history_s50_cli
        print(format_history_s50_cli(limit=int(getattr(args, "last", None) or 15)))
        return 0

    if args.command == "research-artifacts":
        from bot.research.market_events.signal_intelligence.research_artifacts_s50 import (
            format_artifacts_report_s50,
        )
        sym = getattr(args, "symbol", None)
        with market_events_readonly_connection() as conn:
            print(format_artifacts_report_s50(conn, symbol=sym))
        return 0

    if args.command == "review":
        if _audit_s42_db_path(command="review") != 0:
            return 1
        from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
            run_review_s40_cli,
        )
        symbol = getattr(args, "symbol", None) or getattr(args, "message_text", None)
        try:
            print(run_review_s40_cli(symbol=symbol, last=int(getattr(args, "last", None) or 20)))
        except sqlite3.OperationalError as exc:
            if "review_status" in str(exc) or "review_type" in str(exc):
                print(
                    "Database schema is older than S4.2.\n"
                    "Run:\n"
                    "python -m bot.research.market_events market-event-migrate",
                    file=sys.stderr,
                )
                return 1
            raise
        return 0

    if args.command == "learning-worker":
        if _audit_s42_db_path(command="learning-worker") != 0:
            return 1
        from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
            run_learning_worker_s40,
        )
        try:
            stats = run_learning_worker_s40(
                max_cycles=args.max_cycles,
                max_reviews_per_cycle=int(getattr(args, "max_reviews_per_cycle", None) or 5),
            )
        except sqlite3.OperationalError as exc:
            if "review_status" in str(exc) or "review_type" in str(exc):
                print(
                    "Database schema is older than S4.2.\n"
                    "Run:\n"
                    "python -m bot.research.market_events market-event-migrate",
                    file=sys.stderr,
                )
                return 1
            raise
        print(
            "S4.1 cycles={cycles} ingested={ingested} checkpoints={checkpoints_written} "
            "reviews={reviews_written} paper_opened={paper_opened} paper_ticked={paper_ticked} "
            "errors={errors}".format(**stats),
        )
        return 1 if stats["errors"] and not stats["ingested"] and not stats["reviews_written"] else 0

    if args.command == "paper-performance":
        if _audit_s42_db_path(command="paper-performance") != 0:
            return 1
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            format_paper_performance_s42,
        )
        symbol = (args.symbol or None)
        if symbol:
            symbol = symbol.upper().replace("USDT", "")
        elif getattr(args, "message_text", None) and not args.today and not args.week:
            symbol = str(args.message_text).upper().replace("USDT", "")
        try:
            with market_events_readonly_connection() as conn:
                print(format_paper_performance_s42(
                    conn,
                    symbol=symbol,
                    today=bool(args.today),
                    week=bool(args.week),
                ))
        except sqlite3.OperationalError as exc:
            if "review_status" in str(exc) or "review_type" in str(exc):
                print(
                    "Database schema is older than S4.2.\n"
                    "Run:\n"
                    "python -m bot.research.market_events market-event-migrate",
                    file=sys.stderr,
                )
                return 1
            raise
        return 0

    if args.command == "paper-gate-funnel":
        from bot.research.market_events.signal_intelligence.paper_gate_funnel_s55 import (
            format_paper_gate_funnel,
        )

        hours = float(getattr(args, "hours", None) or 24)
        with market_events_readonly_connection() as conn:
            print(format_paper_gate_funnel(conn, since_hours=hours, limit_details=100))
        return 0

    if args.command == "trade-regression-audit":
        if _audit_s42_db_path(command="trade-regression-audit") != 0:
            return 1
        from bot.research.market_events.signal_intelligence.trade_regression_audit_s55 import (
            format_trade_regression_audit,
            run_trade_regression_audit,
        )
        deploy_ts = int(args.start) if args.start is not None else None
        top_n = 100
        try:
            with market_events_readonly_connection() as conn:
                if args.json:
                    print(json.dumps(run_trade_regression_audit(conn, deploy_ts=deploy_ts, top_n=top_n), indent=2, default=str))
                else:
                    print(format_trade_regression_audit(conn, deploy_ts=deploy_ts, top_n=top_n))
        except Exception as exc:
            print(f"trade-regression-audit failed: {exc}")
            return 1
        return 0

    if args.command == "trade-postmortem":
        if _audit_s42_db_path(command="trade-postmortem") != 0:
            return 1
        from bot.research.market_events.db import retry_on_db_locked
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            apply_research_migrations,
            research_connection,
        )
        from bot.research.market_events.signal_intelligence.trade_postmortem_s56 import (
            backfill_snapshots,
            diagnose_snapshots,
            format_postmortem_report,
            run_postmortem,
        )
        try:
            def _run() -> int:
                with market_events_connection() as live:
                    with research_connection() as conn:
                        apply_research_migrations(conn)
                        do_backfill = bool(args.backfill) or args.backfill_last is not None
                        if do_backfill:
                            limit = int(args.backfill_last) if args.backfill_last is not None else None
                            result = backfill_snapshots(
                                conn, limit=limit, run_rca=True, live_conn=live,
                            )
                            conn.commit()
                            if args.json:
                                print(json.dumps(result, indent=2, default=str))
                            else:
                                print(
                                    f"backfill written={result.get('written')} "
                                    f"failed={result.get('failed')} "
                                    f"skipped={result.get('skipped')} "
                                    f"candidates={result.get('candidates')} "
                                    f"snapshots_total={result.get('snapshots_total')}"
                                )
                                print("")
                                print(format_postmortem_report(conn))
                            return 0
                        if args.force:
                            result = run_postmortem(conn, force=True)
                            conn.commit()
                            if args.json:
                                print(json.dumps(result, indent=2, default=str))
                            else:
                                print(format_postmortem_report(conn))
                            return 0
                        conn.commit()
                        diag = diagnose_snapshots(conn, live_conn=live)
                        if args.json:
                            print(json.dumps({
                                "diagnose": diag,
                                "report": format_postmortem_report(conn),
                            }, indent=2, default=str))
                        else:
                            print(format_postmortem_report(conn))
                        return 0

            return int(retry_on_db_locked(_run))
        except Exception as exc:
            print(f"trade-postmortem failed: {exc}")
            return 1

    if args.command == "market-research-migrate":
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            get_research_repository,
        )
        try:
            repo = get_research_repository()
            result = repo.migrate()
            if args.json:
                print(json.dumps({**result, **repo.status()}, indent=2, default=str))
            else:
                print(
                    f"market-research-migrate ok backend={result.get('backend')} "
                    f"source={result.get('url_source')} separated={result.get('separated')} "
                    f"applied={result.get('applied')} schema={result.get('schema_version')}"
                )
            return 0
        except Exception as exc:
            print(f"market-research-migrate failed: {exc}")
            return 1

    if args.command == "research-stress-test":
        from bot.research.market_events.signal_intelligence.research_stress_s60 import (
            format_research_stress_report,
            run_research_stress_test,
        )
        workers = int(getattr(args, "workers", None) or 100)
        try:
            result = run_research_stress_test(workers=workers, live_ticks=max(20, workers // 2))
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(format_research_stress_report(result))
            return 0 if result.get("ok") else 1
        except Exception as exc:
            print(f"research-stress-test failed: {exc}")
            return 1

    if args.command == "market-regime":
        if _audit_s42_db_path(command="market-regime") != 0:
            return 1
        from bot.research.market_events.db import retry_on_db_locked
        from bot.research.market_events.signal_intelligence.market_regime_s57 import (
            backfill_regimes,
            compute_regime_stats,
            format_regime_report,
            run_regime_analysis,
        )
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            apply_research_migrations,
            research_connection,
        )
        try:
            def _run_regime() -> int:
                with research_connection() as conn:
                    apply_research_migrations(conn)
                    if args.backfill or args.backfill_last is not None:
                        limit = int(args.backfill_last) if args.backfill_last is not None else None
                        result = backfill_regimes(conn, limit=limit)
                        conn.commit()
                        if args.json:
                            print(json.dumps(result, indent=2, default=str))
                        else:
                            print(
                                f"regime backfill snapshots={result.get('updated_snapshots')} "
                                f"features={result.get('updated_features')}"
                            )
                    if args.force or args.llm or args.write_suggestions:
                        out = run_regime_analysis(
                            conn,
                            with_llm=bool(args.llm),
                            write_suggestions=True if args.write_suggestions else None,
                        )
                        conn.commit()
                        if args.json:
                            print(json.dumps(out, indent=2, default=str))
                        else:
                            print(format_regime_report(conn))
                            print("")
                            print(out.get("llm_text") or "")
                        return 0
                    conn.commit()
                    if args.json:
                        print(json.dumps({
                            "stats": compute_regime_stats(conn),
                            "report": format_regime_report(conn),
                        }, indent=2, default=str))
                    else:
                        print(format_regime_report(conn))
                    return 0

            return int(retry_on_db_locked(_run_regime))
        except Exception as exc:
            print(f"market-regime failed: {exc}")
            return 1

    if args.command == "trade-suggestions":
        if _audit_s42_db_path(command="trade-suggestions") != 0:
            return 1
        from bot.research.market_events.db import retry_on_db_locked
        from bot.research.market_events.signal_intelligence.research_repository_s60 import (
            apply_research_migrations,
            research_connection,
        )
        from bot.research.market_events.signal_intelligence.trade_postmortem_s56 import (
            STATUS_WAITING,
            format_suggestion,
            list_suggestions,
        )

        def _list() -> int:
            with research_connection() as conn:
                apply_research_migrations(conn)
                conn.commit()
                rows = list_suggestions(conn, status=None if args.json else STATUS_WAITING, limit=100)
            if args.json:
                print(json.dumps(rows, indent=2, default=str))
            elif not rows:
                print("No WAITING_APPROVAL suggestions.")
            else:
                for s in rows:
                    print(format_suggestion(s))
                    print("")
            return 0

        try:
            return retry_on_db_locked(_list)
        except Exception as exc:
            print(f"trade-suggestions failed: {exc}")
            return 1

    if args.command == "approve-suggestion":
        if args.suggestion_id is None:
            print("approve-suggestion requires --suggestion-id N", file=sys.stderr)
            return 1
        from bot.research.market_events.db import retry_on_db_locked
        from bot.research.market_events.signal_intelligence.trade_postmortem_s56 import (
            approve_suggestion,
        )

        def _approve() -> int:
            with market_events_connection() as conn:
                apply_migrations(conn)
                out = approve_suggestion(conn, int(args.suggestion_id))
                conn.commit()
            if not out.get("ok"):
                print(f"approve failed: {out}")
                return 1
            print("APPROVED — strategy NOT changed. Cursor task:")
            print(out.get("cursor_task"))
            return 0

        try:
            return retry_on_db_locked(_approve)
        except Exception as exc:
            print(f"approve-suggestion failed: {exc}")
            return 1

    if args.command == "reject-suggestion":
        if args.suggestion_id is None:
            print("reject-suggestion requires --suggestion-id N", file=sys.stderr)
            return 1
        from bot.research.market_events.db import retry_on_db_locked
        from bot.research.market_events.signal_intelligence.trade_postmortem_s56 import (
            reject_suggestion,
        )

        def _reject() -> int:
            with market_events_connection() as conn:
                apply_migrations(conn)
                out = reject_suggestion(conn, int(args.suggestion_id))
                conn.commit()
            print(json.dumps(out, indent=2) if args.json else out)
            return 0 if out.get("ok") else 1

        try:
            return retry_on_db_locked(_reject)
        except Exception as exc:
            print(f"reject-suggestion failed: {exc}")
            return 1

    if args.command == "reversal-diagnostics":
        from bot.research.market_events.signal_intelligence.reversal_diagnostics_s21 import (
            format_reversal_diagnostics_s21,
        )
        limit = max(50, int(getattr(args, "limit", 500) or 500))
        with market_events_readonly_connection() as conn:
            print(format_reversal_diagnostics_s21(conn, limit=limit))
        return 0

    if args.command == "pipeline-audit":
        from bot.research.market_events.signal_intelligence.pipeline_audit_g0 import (
            format_pipeline_audit_g0,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_pipeline_audit_g0(conn))
        return 0

    if args.command == "recorder-debug":
        from bot.research.market_events.signal_intelligence.pipeline_audit_g0 import (
            format_recorder_debug_g0,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_recorder_debug_g0(conn))
        return 0

    if args.command == "telegram-debug":
        from bot.research.market_events.signal_intelligence.pipeline_audit_g0 import (
            format_telegram_debug_g0,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_telegram_debug_g0(conn))
        return 0

    if args.command == "telegram-inbound-debug":
        from bot.research.market_events.signal_intelligence.telegram_inbound_g04 import (
            format_inbound_debug_g04,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_inbound_debug_g04(conn, limit=20))
        return 0

    if args.command == "telegram-self-test":
        from bot.research.market_events.signal_intelligence.telegram_inbound_g04 import (
            format_telegram_self_test_g04,
        )
        print(format_telegram_self_test_g04())
        return 0

    if args.command == "simulate-telegram-message":
        from bot.research.market_events.signal_intelligence.telegram_inbound_g04 import (
            simulate_telegram_message_g04,
        )
        body = args.message or args.message_text
        if not body:
            print("Usage: simulate-telegram-message --message '...text...'")
            return 2
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        print(simulate_telegram_message_g04(body))
        return 0

    if args.command == "emit-test-signal":
        from bot.research.market_events.signal_intelligence.pipeline_audit_g0 import (
            count_emit_test_shadow_signals_g0,
            format_emit_test_signal_g0,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_emit_test_signal_g0(conn))
            conn.commit()
            n = count_emit_test_shadow_signals_g0(conn)
            print("")
            print(f"emit-test shadow signals total: {n}")
        return 0

    if args.command == "data-source-debug":
        from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
            format_data_source_debug_g01,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            syms = _parse_symbols(args.symbols) if args.symbols else None
            print(format_data_source_debug_g01(conn, symbols=syms))
        return 0

    if args.command == "volume-debug":
        from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
            format_volume_debug_g01,
        )
        syms = tuple(_parse_symbols(args.symbols)) if args.symbols else ("BTC", "ETH", "SOL", "XRP")
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_volume_debug_g01(conn, symbols=syms))  # type: ignore[arg-type]
            conn.commit()
        return 0

    if args.command == "env-debug":
        from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
            format_env_debug_g02,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_env_debug_g02(conn))
        return 0

    if args.command == "api-test":
        from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
            format_api_test_g02,
        )
        sym = (args.symbol or "BTC").upper()
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_api_test_g02(conn, symbol=sym))
        return 0

    if args.command == "provider-status":
        from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
            format_provider_status_g03,
        )
        sym = (args.symbol or "BTC").upper()
        with market_events_readonly_connection() as conn:
            print(format_provider_status_g03(conn, symbol=sym))
        return 0

    if args.command == "sqlite-lock-debug":
        from bot.research.market_events.sqlite_manager_g05 import format_sqlite_lock_debug_g05
        # Touch a readonly + write briefly so registry has something when idle
        try:
            with market_events_readonly_connection() as conn:
                conn.execute("SELECT 1")
                print(format_sqlite_lock_debug_g05())
        except Exception:
            print(format_sqlite_lock_debug_g05())
        return 0

    if args.command == "sqlite-contention-report":
        import os as _os

        from bot.research.market_events.sqlite_manager_g05 import format_sqlite_contention_report

        window = float(_os.getenv("ME_SQLITE_CONTENTION_WINDOW_SEC", "300"))
        print(format_sqlite_contention_report(window_sec=window))
        return 0

    if args.command == "sqlite-lock-smoke":
        from bot.research.market_events.signal_intelligence.sqlite_lock_smoke_g05 import (
            format_sqlite_lock_smoke_g05,
        )
        print(format_sqlite_lock_smoke_g05(per_command=max(1, args.iterations)))
        return 0

    if args.command == "threshold-optimizer":
        from bot.research.market_events.signal_intelligence.threshold_optimizer_g32 import (
            format_threshold_optimizer_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_threshold_optimizer_report(conn, days=max(1, args.days)))
        return 0

    if args.command == "system-validation":
        from bot.research.market_events.system_validation.report import format_health_report, report_to_json
        from bot.research.market_events.system_validation.runner import run_system_validation

        with market_events_connection() as conn:
            apply_migrations(conn)
            results = run_system_validation(
                conn,
                days=args.days,
                load_events=args.load_events,
                skip_load=args.skip_load,
                skip_mutating=args.read_only,
            )
            if args.json:
                print(report_to_json(results))
            else:
                print(format_health_report(results))
            failed = sum(1 for r in results if r.status == "FAIL")
            return 1 if failed else 0

    if args.command == "dashboard-api-serve":
        from bot.research.market_events.alert_engine.config import DASHBOARD_API_HOST, DASHBOARD_API_PORT
        from bot.research.market_events.alert_engine.dashboard_api import run_dashboard_api
        run_dashboard_api(host=DASHBOARD_API_HOST, port=args.port or DASHBOARD_API_PORT)
        return 0

    if args.command == "market-heartbeat-preview":
        from bot.research.market_events.alert_engine.heartbeat import build_heartbeat_message
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(build_heartbeat_message(conn))
        return 0

    if args.command == "market-daily-digest":
        from bot.research.market_events.alert_engine.daily_digest import build_daily_digest, send_daily_digest
        with market_events_connection() as conn:
            apply_migrations(conn)
            msg, _ = build_daily_digest(conn)
            print(msg)
            if os.getenv("ME_DAILY_DIGEST_SEND", "").lower() in ("1", "true", "yes"):
                send_daily_digest(conn)
        return 0

    if args.command == "market-weekly-report":
        from bot.research.market_events.alert_engine.weekly_report import build_weekly_report, send_weekly_report
        with market_events_connection() as conn:
            apply_migrations(conn)
            msg, _ = build_weekly_report(conn)
            print(msg)
            if os.getenv("ME_WEEKLY_DIGEST_SEND", "").lower() in ("1", "true", "yes"):
                send_weekly_report(conn)
        return 0

    if args.command == "market-timeline-report":
        from bot.research.market_events.alert_engine.timeline import (
            build_event_timeline,
            format_timeline_text,
        )
        if not args.event_id:
            print("market-timeline-report requires --event-id")
            return 1
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_timeline_text(build_event_timeline(conn, event_id=args.event_id)))
        return 0

    if args.command == "market-opportunity-report":
        from bot.research.market_events.alert_engine.opportunity_score import compute_opportunity_score
        with market_events_connection() as conn:
            apply_migrations(conn)
            if args.event_id:
                print(json.dumps(compute_opportunity_score(conn, event_id=args.event_id), indent=2))
            else:
                rows = conn.execute(
                    """
                    SELECT o.*, e.symbol FROM market_events_opportunity_scores o
                    JOIN market_events e ON e.id = o.event_id
                    ORDER BY o.score DESC LIMIT 20
                    """,
                ).fetchall()
                for r in rows:
                    print(f"  {r['symbol']} event={r['event_id']} score={r['score']}")
        return 0

    if args.command == "market-ai-comparison-report":
        from bot.research.market_events.alert_engine.ai_comparison import comparison_report
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(comparison_report(conn, days=args.days))
        return 0

    if args.command == "historical-replay-coverage":
        from bot.research.market_events.historical_replay.coverage import historical_replay_coverage
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(historical_replay_coverage(conn))
        return 0

    if args.command == "historical-candle-coverage":
        from bot.research.market_events.historical_replay.candle_backfill import historical_candle_coverage
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(historical_candle_coverage(conn))
        return 0

    if args.command == "historical-candle-backfill":
        import time as _time
        from bot.research.market_events.historical_replay.candle_backfill import run_candle_backfill
        end_ts = args.end or int(_time.time())
        start_ts = args.start or (end_ts - 86400)
        syms = explicit_symbols or []
        if not syms:
            print("historical-candle-backfill requires --symbols (not auto-started)")
            return 1
        with market_events_connection() as conn:
            apply_migrations(conn)
            stats = run_candle_backfill(
                conn,
                symbols=syms,
                asset_class=args.asset_class,
                start_ts=start_ts,
                end_ts=end_ts,
                timeframe=args.timeframe,
            )
            print(stats)
        return 0

    if args.command == "historical-shock-replay":
        from bot.research.market_events.historical_replay.runner import run_full_replay_pipeline
        with market_events_connection() as conn:
            apply_migrations(conn)
            stats = run_full_replay_pipeline(
                conn, run_tag=args.run_tag, days=args.days, symbols=explicit_symbols,
            )
            print(f"HISTORICAL REPLAY complete: {stats}")
        return 0

    if args.command in (
        "historical-shock-report",
        "historical-reversal-report",
        "historical-context-report",
        "ai-critic-replay-report",
    ):
        with market_events_connection() as conn:
            apply_migrations(conn)
            if args.command == "historical-shock-report":
                from bot.research.market_events.historical_replay.reports import historical_shock_report
                print(historical_shock_report(conn, run_tag=args.run_tag))
            elif args.command == "historical-reversal-report":
                from bot.research.market_events.historical_replay.reports import historical_reversal_report
                print(historical_reversal_report(conn, run_tag=args.run_tag))
            elif args.command == "historical-context-report":
                from bot.research.market_events.historical_replay.reports import historical_context_report
                print(historical_context_report(conn, run_tag=args.run_tag))
            elif args.command == "ai-critic-replay-report":
                from bot.research.market_events.historical_replay.ai_critic import ai_critic_replay_report
                print(ai_critic_replay_report(conn, run_tag=args.run_tag))
        return 0

    if args.command == "historical-strategy-matrix":
        from bot.research.market_events.historical_replay.strategy_matrix import (
            run_strategy_matrix,
            strategy_matrix_report,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            stats = run_strategy_matrix(conn, run_tag=args.run_tag)
            print(strategy_matrix_report(conn, run_tag=args.run_tag))
            print(f"matrix_stats: {stats}")
        return 0

    if args.command in ("architecture-audit", "cli-architecture-audit"):
        from bot.research.market_events.signal_intelligence.cli_architecture_audit_s601 import (
            format_cli_architecture_audit,
            run_cli_architecture_audit,
            write_audit_markdown,
        )
        from pathlib import Path

        audit = run_cli_architecture_audit()
        if args.json:
            print(json.dumps(audit.to_dict(), indent=2, default=str))
        else:
            print(format_cli_architecture_audit(audit))
        if getattr(args, "write", False):
            out = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "docs"
                / "research"
                / "S60_1_CLI_ARCHITECTURE_AUDIT.md"
            )
            write_audit_markdown(audit, out)
            print(f"\nWrote {out}")
        return 0 if audit.ok else 1

    if args.command == "polymarket-paper-audit":
        from bot.research.market_events.polymarket_audit import render_polymarket_paper_audit
        print(render_polymarket_paper_audit())
        return 0

    if args.command == "e2-audit":
        from pathlib import Path
        doc = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "research" / "PHASE_E2_ARCHITECTURE.md"
        print(doc.read_text() if doc.exists() else "See PHASE_E2_ARCHITECTURE.md")
        return 0

    if args.command == "index-discovery-audit":
        from bot.research.market_events.index_discovery_audit import audit_bybit_index_discovery
        audit = audit_bybit_index_discovery()
        print(json.dumps({
            "bybit_index_legacy_count": audit.bybit_index_count,
            "bybit_etf_proxy_count": audit.etf_proxy_count,
            "legacy_map_misses": audit.legacy_map_misses,
            "legacy_map_hits": audit.legacy_map_hits,
            "symbol_type_index_count": audit.symbol_type_index_count,
            "etf_proxy_hits": audit.etf_proxy_hits,
            "findings": audit.findings,
            "spx_meme_warning": audit.spx_meme_warning,
        }, indent=2))
        return 0

    if args.command == "instrument-discover":
        from bot.research.market_events.instrument_discovery import run_instrument_discovery
        from bot.research.market_events.instrument_report import instrument_registry_report

        with market_events_connection() as conn:
            apply_migrations(conn)
            result = run_instrument_discovery(conn, enable_tradfi=args.enable_tradfi)
            print(instrument_registry_report(conn))
            print("")
            print(
                f"discovered: binance={result.binance_count} equity={result.bybit_stock_count} "
                f"commodity={result.bybit_commodity_count} index_legacy={result.bybit_index_count} "
                f"etf_proxy={result.bybit_etf_proxy_count} paper_active={result.paper_active_count} "
                f"watch={result.watch_count}",
            )
        return 0

    if args.command == "instrument-report":
        from bot.research.market_events.instrument_report import instrument_registry_report

        with market_events_connection() as conn:
            apply_migrations(conn)
            print(instrument_registry_report(conn))
        return 0

    if args.command == "activation-explain":
        from bot.research.market_events.activation_explain import explain_instrument_activation

        with market_events_connection() as conn:
            apply_migrations(conn)
            print(explain_instrument_activation(conn, canonical=args.symbol))
        return 0

    if args.command == "observe-report":
        from bot.research.market_events.observation_report import observation_report

        with market_events_connection() as conn:
            apply_migrations(conn)
            print(observation_report(conn, days=args.days))
        return 0

    if args.command in ("db-info", "market-db-info"):
        from bot.research.market_events.db_tools import format_db_info
        print(format_db_info())
        return 0

    if args.command == "market-db-check":
        from bot.research.market_events.db_tools import db_check
        print(db_check())
        return 0

    if args.command in ("market-db-copy", "migrate-to-postgres"):
        from bot.research.market_events.db_tools import migrate_sqlite_to_postgres
        print(migrate_sqlite_to_postgres())
        return 0

    if args.command == "market-db-benchmark":
        from bot.research.market_events.db_tools import db_benchmark
        print(db_benchmark(n=args.load_events))
        return 0

    if args.command == "market-db-backup":
        from bot.research.market_events.db_tools import db_backup
        dest = Path(args.file) if args.file else None
        print(db_backup(dest=dest))
        return 0

    if args.command == "market-db-restore":
        from bot.research.market_events.db_tools import db_restore
        archive = Path(args.file) if args.file else None
        print(db_restore(archive=archive))
        return 0

    if args.command == "market-event-migrate":
        from bot.research.market_events.db_config import resolve_market_events_db_config
        mode = ensure_db_initialized()
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
        cfg = resolve_market_events_db_config()
        print(f"Backend: {cfg.backend}")
        print(f"DB mode: {mode}")
        print(f"Migrations applied: {applied or ['already up to date']}")
        return 0

    if args.command == "observe-run":
        from bot.research.market_events.observation_runner import run_observe
        universe = args.universe or "tradfi-observe"
        stats = run_observe(
            universe=universe,
            explicit_symbols=explicit_symbols,
            max_cycles=args.max_cycles,
        )
        return 1 if stats.errors else 0

    if args.command == "shock-paper-run":
        from bot.research.market_events.paper_runner import run_shock_paper
        universe = args.universe or "core"
        stats = run_shock_paper(
            universe=universe,
            paper_only=True,
            max_cycles=args.max_cycles,
            explicit_symbols=explicit_symbols,
            heartbeat_sec=args.heartbeat_sec,
        )
        return 1 if stats.errors else 0

    with market_events_connection() as conn:
        apply_migrations(conn)
        if args.command == "shock-event-report":
            from bot.research.market_events.event_report import shock_event_report
            print(shock_event_report(conn, days=args.days))
        elif args.command == "shock-strategy-report":
            from bot.research.market_events.event_report import shock_strategy_report
            print(shock_strategy_report(conn, strategy_prefix=args.strategy, days=args.days))
        elif args.command == "shock-context-report":
            from bot.research.market_events.event_report import shock_context_report
            print(shock_context_report(conn, days=args.days))
        elif args.command == "shock-lifecycle-audit":
            from bot.research.market_events.lifecycle_audit import shock_lifecycle_audit
            print(shock_lifecycle_audit(conn, days=args.days))
        elif args.command == "shock-opportunity-audit":
            from bot.research.market_events.shock_opportunity_audit import shock_opportunity_audit
            print(shock_opportunity_audit(conn, days=args.days))
        elif args.command == "shock-f-shadow-audit":
            from bot.research.market_events.shock_f_shadow import run_shock_f_shadow_audit
            print(run_shock_f_shadow_audit(conn, days=args.days))
        elif args.command == "shock-f-v2-shadow-audit":
            from bot.research.market_events.shock_f_v2_shadow import run_shock_f_v2_shadow_audit
            print(run_shock_f_v2_shadow_audit(conn, days=args.days))
        elif args.command == "tradfi-shock-readiness":
            from bot.research.market_events.tradfi_shock_readiness import tradfi_shock_readiness
            print(tradfi_shock_readiness(conn, days=args.days))
        elif args.command == "shock-strategy-matrix-report":
            from bot.research.market_events.strategy_matrix_report import shock_strategy_matrix_report
            print(shock_strategy_matrix_report(conn, days=args.days))
        elif args.command == "pending-reversal-report":
            from bot.research.market_events.pending_reversal_report import pending_reversal_report
            print(pending_reversal_report(conn, days=args.days))
        elif args.command == "shock-profile-report":
            from bot.research.market_events.profile_shadow import (
                scan_profile_shadow_candidates,
                shock_profile_report,
            )
            scan_profile_shadow_candidates(conn, days=args.days, persist=True)
            print(shock_profile_report(conn, days=args.days))
        elif args.command == "reversal-counterfactual-report":
            from bot.research.market_events.counterfactual_reversal import (
                reversal_counterfactual_report,
                run_counterfactual_study,
            )
            run_counterfactual_study(conn, days=args.days, persist=True)
            print(reversal_counterfactual_report(conn, days=args.days))
        elif args.command == "shock-near-miss-report":
            from bot.research.market_events.near_miss_shadow import shock_near_miss_report
            print(shock_near_miss_report(conn, days=args.days, persist=True))
        elif args.command == "collector-path-audit":
            from bot.research.market_events.collector_path_audit import run_collector_path_audit
            universe = args.universe or "tradfi-liquid"
            print(run_collector_path_audit(
                conn, universe=universe, seconds=args.seconds, explicit_symbols=explicit_symbols,
            ))
        elif args.command == "market-alert-audit":
            from bot.research.market_events.ai_analyst.reports import market_alert_audit
            print(market_alert_audit(conn, days=args.days))
        elif args.command == "ai-analysis-audit":
            from bot.research.market_events.ai_analyst.reports import ai_analysis_audit
            print(ai_analysis_audit(conn, days=args.days))
        elif args.command == "ai-event-report":
            from bot.research.market_events.ai_analyst.reports import ai_event_report
            print(ai_event_report(conn, days=args.days))
        elif args.command == "ai-analyze-pending":
            from bot.research.market_events.ai_analyst.job_queue import process_pending_jobs
            n = process_pending_jobs(conn)
            conn.commit()
            print(f"Processed {n} analysis job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
