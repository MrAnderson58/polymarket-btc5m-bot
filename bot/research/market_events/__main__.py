"""Phase E.1/E.2/E.2.1/E.2.2 CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations


def _parse_symbols(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


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
            "ai-worker-run",
            "telegram-alert-test",
            "ai-test",
            "demo-event",
            "telegram-health",
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
            "shadow-open",
            "shadow-trace",
            "shadow-self-test",
            "validation-open",
            "validation-report",
            "validation-force",
            "decision",
            "explain-decision",
            "pattern",
            "pattern-build",
            "reversal-diagnostics",
            "signal-inbox",
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
    parser.add_argument("--heartbeat-sec", type=int, default=None, help="Heartbeat interval (default 60)")
    parser.add_argument("--seconds", type=int, default=30, help="Duration for collector-path-audit")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--hours", type=int, default=24, help="Hours of candle history (G3.8)")
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
    parser.add_argument("--run-tag", default="e4_default", help="Historical replay run tag")
    parser.add_argument("--asset-class", default=None, help="Asset class filter (CRYPTO, EQUITY, …)")
    parser.add_argument("--start", type=int, default=None, help="Backfill start unix ts")
    parser.add_argument("--end", type=int, default=None, help="Backfill end unix ts")
    parser.add_argument("--timeframe", default="1m", help="Candle timeframe for backfill")
    parser.add_argument("--event-id", type=int, default=None, help="Event id for timeline/opportunity reports")
    parser.add_argument("--port", type=int, default=None, help="Dashboard API port")
    parser.add_argument("--read-only", action="store_true", help="Skip mutating validation checks")
    parser.add_argument("--skip-load", action="store_true", help="Skip synthetic load test")
    parser.add_argument("--load-events", type=int, default=200, help="Synthetic load test event count")
    parser.add_argument("--json", action="store_true", help="JSON output for system-validation")
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
        "--iterations",
        type=int,
        default=100,
        help="sqlite-lock-smoke: commands per type (default 100)",
    )
    args = parser.parse_args(argv)
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

    if args.command == "telegram-alert-test":
        from bot.research.market_events.telegram_ops.cli import run_telegram_alert_test
        with market_events_connection() as conn:
            apply_migrations(conn)
            code, text = run_telegram_alert_test(conn)
            conn.commit()
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
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(run_telegram_health(conn))
        return 0

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
        with market_events_connection() as conn:
            apply_migrations(conn)
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
        from bot.research.market_events.db import market_events_readonly_connection
        symbol = (args.symbol or "BTC").upper().replace("USDT", "")
        # S2.2: READ ONLY — use readonly connection, never persist.
        with market_events_readonly_connection() as conn:
            result = run_decision_engine_s20(conn, symbol, persist=False)
            print(result["telegram"])
        return 0

    if args.command == "explain-decision":
        from bot.research.market_events.db import market_events_readonly_connection
        from bot.research.market_events.signal_intelligence.explain_decision_s22 import (
            format_explain_decision_s22,
        )
        symbol = (args.symbol or "BTC").upper().replace("USDT", "")
        with market_events_readonly_connection() as conn:
            print(format_explain_decision_s22(conn, symbol))
        return 0

    if args.command == "pattern":
        from bot.research.market_events.db import market_events_readonly_connection
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
        with market_events_readonly_connection() as conn:
            result = run_pattern_agent_s31(conn, symbol=symbol, timeframe=tf)
            print(format_pattern_report_s31(result))
        return 0

    if args.command == "pattern-build":
        from bot.research.market_events.db import market_events_readonly_connection
        from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
            build_pattern_index_s31,
            format_pattern_build_report_s31,
        )
        with market_events_readonly_connection() as conn:
            index = build_pattern_index_s31(conn)
            print(format_pattern_build_report_s31(index))
        return 0

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

    if args.command == "reversal-diagnostics":
        from bot.research.market_events.signal_intelligence.reversal_diagnostics_s21 import (
            format_reversal_diagnostics_s21,
        )
        limit = max(50, int(getattr(args, "limit", 500) or 500))
        with market_events_connection() as conn:
            apply_migrations(conn)
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
        with market_events_connection() as conn:
            apply_migrations(conn)
            print(format_provider_status_g03(conn, symbol=sym))
        return 0

    if args.command == "sqlite-lock-debug":
        from bot.research.market_events.sqlite_manager_g05 import format_sqlite_lock_debug_g05
        # Touch a readonly + write briefly so registry has something when idle
        try:
            from bot.research.market_events.db import market_events_readonly_connection
            with market_events_readonly_connection() as conn:
                conn.execute("SELECT 1")
                print(format_sqlite_lock_debug_g05())
        except Exception:
            print(format_sqlite_lock_debug_g05())
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

    if args.command == "architecture-audit":
        from pathlib import Path
        doc = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "research" / "PHASE_E1_ARCHITECTURE_AUDIT.md"
        if doc.exists():
            print(doc.read_text())
        else:
            print("See docs/research/PHASE_E1_ARCHITECTURE_AUDIT.md")
        return 0

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
        from bot.research.market_events.db import ensure_db_initialized
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
