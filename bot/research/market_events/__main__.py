"""Phase E.1/E.2/E.2.1/E.2.2 CLI."""

from __future__ import annotations

import argparse
import os
import sys

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
                import json
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
        import json
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

    if args.command == "market-event-migrate":
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
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
