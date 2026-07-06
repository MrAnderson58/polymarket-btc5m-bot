"""Market behavior research CLI.

Usage:
  python -m bot.research.market_behavior report
  python -m bot.research.market_behavior edge-report
  python -m bot.research.market_behavior heatmap

Read-only. Analyzes existing v4_shadow_observations history.
Does not modify trading logic, collector, or execution tables.
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe-only Polymarket market behavior research",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    report_p = sub.add_parser("report", help="Analyze snapshots and print summary report")
    report_p.add_argument("--min-obs", type=int, default=None)
    report_p.add_argument("--limit", type=int, default=None)
    report_p.add_argument("--no-persist", action="store_true")

    edge_p = sub.add_parser("edge-report", help="Multidimensional edge statistics report")
    edge_p.add_argument("--min-obs", type=int, default=None)
    edge_p.add_argument("--limit", type=int, default=None)
    edge_p.add_argument("--min-samples", type=int, default=None)
    edge_p.add_argument("--top", type=int, default=10)
    edge_p.add_argument("--no-persist", action="store_true")

    heat_p = sub.add_parser("heatmap", help="Text heatmaps: entry/time x BTC delta")
    heat_p.add_argument("--min-obs", type=int, default=None)
    heat_p.add_argument("--limit", type=int, default=None)
    heat_p.add_argument("--min-samples", type=int, default=10)
    heat_p.add_argument("--no-persist", action="store_true")

    args = parser.parse_args()

    from bot.database import connect, init_db
    from bot.research.market_behavior.schema import ensure_tables

    init_db()

    if args.command == "report":
        from bot.research.market_behavior.engine import run_analysis
        from bot.research.market_behavior.report import render_report

        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            report = run_analysis(
                conn,
                min_obs=args.min_obs,
                market_limit=args.limit,
                persist=not args.no_persist,
            )
        print(render_report(report))
        return 0

    if args.command in ("edge-report", "heatmap"):
        from bot.research.market_behavior.edge_engine import run_edge_analysis
        from bot.research.market_behavior.edge_report import (
            render_edge_report,
            render_full_heatmap,
        )
        from bot.research.market_behavior.edge_storage import load_edge_cells

        with connect() as conn:
            ensure_tables(conn)
            conn.commit()
            if args.no_persist:
                cells = run_edge_analysis(
                    conn,
                    min_obs=args.min_obs,
                    market_limit=args.limit,
                    persist=False,
                )
            else:
                run_edge_analysis(
                    conn,
                    min_obs=args.min_obs,
                    market_limit=args.limit,
                    persist=True,
                )
                cells = load_edge_cells(conn)

        if args.command == "edge-report":
            print(render_edge_report(
                cells,
                min_samples=args.min_samples,
                top_n=args.top,
            ))
        else:
            print(render_full_heatmap(cells, min_samples=args.min_samples))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
