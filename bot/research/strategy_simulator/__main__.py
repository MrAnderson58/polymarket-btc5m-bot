"""Strategy simulator CLI.

Usage:
  python -m bot.research.strategy_simulator simulate
  python -m bot.research.strategy_simulator discover

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
    sim_p.add_argument("--one-per-market", action="store_true")
    sim_p.add_argument("--no-persist", action="store_true")

    disc_p = sub.add_parser("discover", help="Grid search historical strategies")
    disc_p.add_argument("--min-obs", type=int, default=None)
    disc_p.add_argument("--limit", type=int, default=None)
    disc_p.add_argument("--min-trades", type=int, default=None)
    disc_p.add_argument("--top", type=int, default=20)
    disc_p.add_argument("--no-persist", action="store_true")

    args = parser.parse_args()

    from bot.database import connect, init_db
    from bot.research.strategy_simulator.engine import run_discovery, run_simulation
    from bot.research.strategy_simulator.report import render_discovery_report, render_simulation_report
    from bot.research.strategy_simulator.storage import ensure_tables

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
                one_trade_per_market=args.one_per_market,
                persist=not args.no_persist,
            )
        print(render_simulation_report(stats, trades))
        return 0

    if args.command == "discover":
        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            ranked = run_discovery(
                conn,
                min_obs=args.min_obs,
                market_limit=args.limit,
                min_trades=args.min_trades,
                top_n=args.top,
                persist=not args.no_persist,
            )
        print(render_discovery_report(
            ranked,
            min_trades=args.min_trades or MIN_TRADES_FOR_RANK,
        ))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
