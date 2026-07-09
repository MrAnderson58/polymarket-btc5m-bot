"""Phase E.1 CLI."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase E.1 market shock paper research")
    parser.add_argument(
        "command",
        choices=(
            "market-event-migrate",
            "shock-paper-run",
            "shock-event-report",
            "shock-strategy-report",
            "shock-context-report",
            "polymarket-paper-audit",
            "architecture-audit",
        ),
    )
    parser.add_argument("--universe", default="core", help="Universe mode (core)")
    parser.add_argument("--paper-only", action="store_true", default=True)
    parser.add_argument("--max-cycles", type=int, default=None, help="Limit poll cycles (testing)")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--strategy", type=str, default=None, help="Strategy name prefix filter")
    args = parser.parse_args(argv)

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

    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.event_schema import apply_migrations

    if args.command == "market-event-migrate":
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
        print(f"Migrations applied: {applied or ['already up to date']}")
        return 0

    if args.command == "shock-paper-run":
        from bot.research.market_events.paper_runner import run_shock_paper
        stats = run_shock_paper(
            universe=args.universe,
            paper_only=True,
            max_cycles=args.max_cycles,
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
