"""Phase E.1/E.2/E.2.1 CLI."""

from __future__ import annotations

import argparse
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
            "shock-event-report",
            "shock-strategy-report",
            "shock-context-report",
            "polymarket-paper-audit",
            "architecture-audit",
            "instrument-discover",
            "instrument-report",
            "e2-audit",
            "index-discovery-audit",
        ),
    )
    parser.add_argument(
        "--universe",
        default="core",
        help="core | tradfi-liquid | multi-paper | multi (legacy)",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Explicit venue or canonical symbols, comma-separated (e.g. XAUUSDT,NVDAUSDT)",
    )
    parser.add_argument(
        "--enable-tradfi",
        action="store_true",
        help="Allow PAPER_ACTIVE promotion when activation rules pass (not blind enable)",
    )
    parser.add_argument("--paper-only", action="store_true", default=True)
    parser.add_argument("--max-cycles", type=int, default=None, help="Limit poll cycles (testing)")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--strategy", type=str, default=None, help="Strategy name prefix filter")
    args = parser.parse_args(argv)
    explicit_symbols = _parse_symbols(args.symbols)

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

    if args.command == "market-event-migrate":
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
        print(f"Migrations applied: {applied or ['already up to date']}")
        return 0

    if args.command == "observe-run":
        from bot.research.market_events.observation_runner import run_observe
        stats = run_observe(max_cycles=args.max_cycles)
        return 1 if stats.errors else 0

    if args.command == "shock-paper-run":
        from bot.research.market_events.paper_runner import run_shock_paper
        stats = run_shock_paper(
            universe=args.universe,
            paper_only=True,
            max_cycles=args.max_cycles,
            explicit_symbols=explicit_symbols,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
