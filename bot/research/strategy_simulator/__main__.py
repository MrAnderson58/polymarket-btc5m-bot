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
from bot.research.strategy_simulator.market_filter import add_market_filter_args, market_filter_from_args
from bot.research.strategy_simulator.strategies import Strategy


def _add_filter_args(parser: argparse.ArgumentParser) -> None:
    add_market_filter_args(parser)


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
    _add_filter_args(sim_p)

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
    _add_filter_args(disc_p)

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
    wf_p.add_argument(
        "--force-export-shadow",
        action="store_true",
        help="Bypass diagnostics gate (not recommended)",
    )
    _add_filter_args(wf_p)

    diag_p = sub.add_parser(
        "split-diagnostics",
        help="Diagnose walk-forward test collapse (data + opportunity funnel)",
    )
    diag_p.add_argument("--min-obs", type=int, default=None)
    diag_p.add_argument("--max-markets", type=int, default=None)
    diag_p.add_argument("--top", type=int, default=20)
    diag_p.add_argument("--min-trades", type=int, default=None)
    diag_p.add_argument("--train-ratio", type=float, default=0.60)
    diag_p.add_argument("--validation-ratio", type=float, default=0.20)
    diag_p.add_argument("--test-ratio", type=float, default=0.20)
    diag_p.add_argument("--rolling-folds", type=int, default=5)
    diag_p.add_argument("--no-progress", action="store_true")
    _add_filter_args(diag_p)

    dense_p = sub.add_parser("dense-era", help="Detect dense-era boundary from DB data")
    dense_p.add_argument("--min-obs", type=int, default=60)
    dense_p.add_argument("--max-median-gap", type=float, default=5.0)
    dense_p.add_argument("--min-span", type=int, default=240)
    dense_p.add_argument("--consecutive", type=int, default=5)

    prep_p = sub.add_parser(
        "prepare-forward",
        help="Discover on dense-era markets and register up to 3 forward candidates",
    )
    prep_p.add_argument("--min-obs", type=int, default=60)
    prep_p.add_argument("--max-median-gap", type=float, default=5.0)
    prep_p.add_argument("--min-span", type=int, default=240)
    prep_p.add_argument("--consecutive", type=int, default=5)
    prep_p.add_argument("--top-families", type=int, default=3)
    prep_p.add_argument("--min-trades", type=int, default=None)

    fwd_p = sub.add_parser(
        "forward-track",
        help="Record observe-only forward signals for registered candidates",
    )
    _add_filter_args(fwd_p)
    fwd_p.add_argument(
        "--market-start-ts",
        type=int,
        default=None,
        help="Only markets on/after this window_start (default: dense-era boundary)",
    )

    audit_p = sub.add_parser("quote-audit", help="Bid/ask semantics audit on v4 observations")
    audit_p.add_argument("--sample-markets", type=int, default=50)

    fin_p = sub.add_parser("finalists", help="List shadow candidate finalists")

    sh_p = sub.add_parser("shadow-enable", help="Enable/disable shadow candidate by id")
    sh_p.add_argument("--strategy-id", type=int, required=True)
    sh_p.add_argument("--disable", action="store_true")
    sh_p.add_argument(
        "--force",
        action="store_true",
        help="Bypass diagnostics gate (not recommended before split-diagnostics)",
    )

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
        render_split_diagnostics_report,
        render_walk_forward_report,
    )
    from bot.research.strategy_simulator.split_diagnostics import run_split_diagnostics
    from bot.research.strategy_simulator.storage import ensure_tables
    from bot.research.strategy_simulator.walk_forward import run_walk_forward

    init_db()

    filt = market_filter_from_args(args) if hasattr(args, "market_start_ts") else None

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
                market_filter=filt,
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
                market_filter=filt,
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
                market_filter=filt,
            )
            if args.export_shadow:
                if not args.force_export_shadow:
                    print(
                        "ERROR: --export-shadow blocked. Run split-diagnostics first.\n"
                        "Use --force-export-shadow only if diagnostics passed.",
                        file=__import__("sys").stderr,
                    )
                    return 2
                export_qualified_finalists(conn, results)
        print(render_walk_forward_report(split, results))
        return 0

    if args.command == "split-diagnostics":
        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            diag = run_split_diagnostics(
                conn,
                min_obs=args.min_obs,
                max_markets=args.max_markets,
                train_ratio=args.train_ratio,
                validation_ratio=args.validation_ratio,
                test_ratio=args.test_ratio,
                top_n=args.top,
                min_trades=args.min_trades,
                n_rolling_folds=args.rolling_folds,
                show_progress=not args.no_progress,
                market_filter=filt,
            )
        print(render_split_diagnostics_report(diag))
        return 0

    if args.command == "dense-era":
        from bot.research.strategy_simulator.dense_era import (
            detect_dense_era_boundary,
            render_dense_era_report,
        )

        with connect() as conn:
            boundary = detect_dense_era_boundary(
                conn,
                min_obs=args.min_obs,
                max_median_gap=args.max_median_gap,
                min_span=args.min_span,
                consecutive_required=args.consecutive,
            )
        print(render_dense_era_report(boundary))
        return 0 if boundary.boundary_window_start_ts is not None else 1

    if args.command == "prepare-forward":
        from bot.research.strategy_simulator.dense_era import render_dense_era_report
        from bot.research.strategy_simulator.prepare_forward import prepare_forward_candidates

        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            boundary, fps = prepare_forward_candidates(
                conn,
                min_obs=args.min_obs,
                max_median_gap=args.max_median_gap,
                min_span=args.min_span,
                consecutive_required=args.consecutive,
                top_families=args.top_families,
                min_trades=args.min_trades,
            )
            conn.commit()
        print(render_dense_era_report(boundary))
        print(f"\nRegistered {len(fps)} forward candidate(s) (observe-only, no execution).")
        for fp in fps:
            print(f"  {fp}")
        return 0

    if args.command == "forward-track":
        from bot.research.strategy_simulator.dense_era import detect_dense_era_boundary
        from bot.research.strategy_simulator.forward_tracker import (
            render_forward_status,
            run_forward_tracking,
        )
        from bot.research.strategy_simulator.market_filter import MarketFilter

        with connect() as conn:
            ensure_tables(conn)
            start_ts = args.market_start_ts
            if start_ts is None:
                boundary = detect_dense_era_boundary(conn)
                start_ts = boundary.boundary_window_start_ts
            if start_ts is None:
                print("ERROR: no dense-era boundary; pass --market-start-ts", file=__import__("sys").stderr)
                return 2
            track_filter = MarketFilter(
                market_start_ts=start_ts,
                min_obs_per_market=filt.min_obs_per_market if filt else 60,
                max_median_gap=filt.max_median_gap if filt else 5.0,
                min_coverage_span=filt.min_coverage_span if filt else 240,
                completed_only=True,
            )
            new_recs = run_forward_tracking(conn, market_filter=track_filter)
            conn.commit()
            status = render_forward_status(conn)
        print(f"Recorded {len(new_recs)} new forward signal(s).")
        print(status)
        return 0

    if args.command == "quote-audit":
        from bot.research.quote_semantics import observation_quotes_reversed

        with connect() as conn:
            slugs = conn.execute(
                """
                SELECT market_slug FROM v4_shadow_observations
                GROUP BY market_slug
                ORDER BY market_slug DESC
                LIMIT ?
                """,
                (args.sample_markets,),
            ).fetchall()
            reversed_rows = 0
            total_rows = 0
            for row in slugs:
                raw = conn.execute(
                    "SELECT yes_bid, yes_ask, no_bid, no_ask FROM v4_shadow_observations WHERE market_slug = ?",
                    (row["market_slug"],),
                ).fetchall()
                for r in raw:
                    total_rows += 1
                    if observation_quotes_reversed(dict(r)):
                        reversed_rows += 1
        pct = 100.0 * reversed_rows / total_rows if total_rows else 0.0
        print("QUOTE SEMANTICS AUDIT (raw v4_shadow_observations)")
        print(f"  sample markets: {len(slugs)}")
        print(f"  rows checked: {total_rows}")
        print(f"  rows with bid>ask (reversed labels): {reversed_rows} ({pct:.1f}%)")
        print("  research loaders normalize on read — see docs/research/BID_ASK_AUDIT.md")
        return 0

    if args.command == "finalists":
        with connect() as conn:
            ensure_tables(conn)
            rows = list_finalists(conn)
        print(render_finalists_report(rows))
        return 0

    if args.command == "shadow-enable":
        if not args.disable and not args.force:
            print(
                "ERROR: shadow-enable blocked until split-diagnostics passes. "
                "Use --force to override.",
                file=__import__("sys").stderr,
            )
            return 2
        with connect() as conn:
            ensure_tables(conn)
            shadow_enable(conn, args.strategy_id, enabled=not args.disable)
        print(f"shadow candidate {args.strategy_id} enabled={not args.disable}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
