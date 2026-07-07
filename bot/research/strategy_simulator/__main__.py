"""Strategy simulator CLI.

Usage:
  python -m bot.research.strategy_simulator simulate
  python -m bot.research.strategy_simulator discover
  python -m bot.research.strategy_simulator walk-forward
  python -m bot.research.strategy_simulator finalists
  python -m bot.research.strategy_simulator shadow-enable --strategy-id N

Observe-only forward replay. No execution impact.
"""

from __future__ import annotations

import argparse

from bot.research.strategy_simulator.config import DEFAULT_TP, MIN_TRADES_FOR_RANK
from bot.research.strategy_simulator.strategies import Strategy


def _parse_strategy(args: argparse.Namespace) -> Strategy:
    return Strategy(
        direction=args.direction,
        max_entry=args.max_entry,
        min_delta=args.min_delta,
        max_delta=args.max_delta,
        max_spread=args.max_spread,
        min_seconds_left=args.min_seconds,
        tp=args.tp,
    )


def _add_strategy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--direction", choices=("YES", "NO"), default="YES")
    parser.add_argument("--max-entry", type=float, default=0.30)
    parser.add_argument("--min-delta", type=float, default=25.0)
    parser.add_argument("--max-delta", type=float, default=None)
    parser.add_argument("--max-spread", type=float, default=0.02)
    parser.add_argument("--min-seconds", type=int, default=60)
    parser.add_argument("--tp", type=float, default=DEFAULT_TP)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe-only strategy simulation from historical snapshots",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sim_p = sub.add_parser("simulate", help="Run one strategy across all markets")
    _add_strategy_args(sim_p)
    sim_p.add_argument("--min-obs", type=int, default=None)
    sim_p.add_argument("--limit", type=int, default=None)
    sim_p.add_argument("--max-markets", type=int, default=None)
    sim_p.add_argument("--one-per-market", action="store_true")
    sim_p.add_argument("--no-persist", action="store_true")

    disc_p = sub.add_parser("discover", help="Grid search historical strategies")
    disc_p.add_argument("--min-obs", type=int, default=None)
    disc_p.add_argument("--limit", type=int, default=None)
    disc_p.add_argument("--max-markets", type=int, default=None)
    disc_p.add_argument("--min-trades", type=int, default=None)
    disc_p.add_argument("--top", type=int, default=20)
    disc_p.add_argument("--no-persist", action="store_true")
    disc_p.add_argument("--no-progress", action="store_true")
    disc_p.add_argument("--no-dedupe", action="store_true")
    disc_p.add_argument("--profile", action="store_true")

    wf_p = sub.add_parser("walk-forward", help="Chronological walk-forward validation")
    wf_p.add_argument("--min-obs", type=int, default=None)
    wf_p.add_argument("--max-markets", type=int, default=None)
    wf_p.add_argument("--top", type=int, default=20)
    wf_p.add_argument("--min-trades", type=int, default=None)
    wf_p.add_argument("--train-ratio", type=float, default=0.60)
    wf_p.add_argument("--validation-ratio", type=float, default=0.20)
    wf_p.add_argument("--test-ratio", type=float, default=0.20)
    wf_p.add_argument("--bootstrap-samples", type=int, default=None)
    wf_p.add_argument("--bootstrap-seed", type=int, default=None)
    wf_p.add_argument("--min-test-trades", type=int, default=None)
    wf_p.add_argument("--min-prob-ev-positive", type=float, default=None)
    wf_p.add_argument("--no-progress", action="store_true")
    wf_p.add_argument("--export-shadow", action="store_true")

    fin_p = sub.add_parser("finalists", help="List shadow candidate finalists")

    sh_p = sub.add_parser("shadow-enable", help="Enable/disable shadow candidate by id")
    sh_p.add_argument("--strategy-id", type=int, required=True)
    sh_p.add_argument("--disable", action="store_true")

    args = parser.parse_args()

    from bot.database import connect, init_db
    from bot.research.strategy_simulator.config import (
        BOOTSTRAP_MIN_PROB_EV_POSITIVE,
        BOOTSTRAP_SAMPLES,
        BOOTSTRAP_SEED,
        MIN_FINALIST_TEST_TRADES,
    )
    from bot.research.strategy_simulator.engine import profile_discovery, run_discovery, run_simulation
    from bot.research.strategy_simulator.finalists import (
        export_qualified_finalists,
        list_finalists,
        shadow_enable,
    )
    from bot.research.strategy_simulator.report import (
        render_discovery_report,
        render_finalists_report,
        render_simulation_report,
        render_walk_forward_report,
    )
    from bot.research.strategy_simulator.storage import ensure_tables
    from bot.research.strategy_simulator.walk_forward import run_walk_forward

    init_db()

    if args.command == "simulate":
        strategy = _parse_strategy(args)
        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            stats, trades = run_simulation(
                conn,
                strategy,
                min_obs=args.min_obs,
                market_limit=args.limit,
                max_markets=args.max_markets,
                one_trade_per_market=args.one_per_market,
                persist=not args.no_persist,
            )
        print(render_simulation_report(stats, trades))
        return 0

    if args.command == "discover":
        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            if args.profile:
                profile_discovery(
                    conn,
                    max_markets=args.max_markets or args.limit or 3,
                    top_n=args.top,
                )
                return 0
            ranked, families = run_discovery(
                conn,
                min_obs=args.min_obs,
                market_limit=args.limit,
                max_markets=args.max_markets,
                min_trades=args.min_trades,
                top_n=args.top,
                persist=not args.no_persist,
                show_progress=not args.no_progress,
                dedupe=not args.no_dedupe,
            )
        print(render_discovery_report(
            ranked,
            min_trades=args.min_trades or MIN_TRADES_FOR_RANK,
            families=families,
        ))
        return 0

    if args.command == "walk-forward":
        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            split, results = run_walk_forward(
                conn,
                min_obs=args.min_obs,
                max_markets=args.max_markets,
                train_ratio=args.train_ratio,
                validation_ratio=args.validation_ratio,
                test_ratio=args.test_ratio,
                top_n=args.top,
                min_trades=args.min_trades,
                bootstrap_samples=args.bootstrap_samples or BOOTSTRAP_SAMPLES,
                bootstrap_seed=args.bootstrap_seed or BOOTSTRAP_SEED,
                min_test_trades=args.min_test_trades or MIN_FINALIST_TEST_TRADES,
                min_prob_ev_positive=args.min_prob_ev_positive or BOOTSTRAP_MIN_PROB_EV_POSITIVE,
                show_progress=not args.no_progress,
            )
            if args.export_shadow:
                export_qualified_finalists(conn, results)
        print(render_walk_forward_report(split, results))
        return 0

    if args.command == "finalists":
        with connect() as conn:
            ensure_tables(conn)
            rows = list_finalists(conn)
        print(render_finalists_report(rows))
        return 0

    if args.command == "shadow-enable":
        with connect() as conn:
            ensure_tables(conn)
            shadow_enable(conn, args.strategy_id, enabled=not args.disable)
        print(f"shadow candidate {args.strategy_id} enabled={not args.disable}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
