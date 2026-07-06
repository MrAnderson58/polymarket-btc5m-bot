"""Multi-Timeframe Polymarket Context Research Layer.

Usage:
  python -m bot.research.mtf
  python -m bot.research.mtf diagnose-discovery
  python -m bot.research.mtf audit-production
  python -m bot.research.mtf research-15m
  python -m bot.research.mtf research-1h
  python -m bot.research.mtf research-daily

Does NOT modify execution, Bidirectional V1.1, or ER strategies.
"""

from __future__ import annotations

import argparse


def run_research() -> dict:
    from bot.database import connect, init_db
    from bot.research.mtf.analysis import (
        context_performance_matrix,
        predictive_value_by_timeframe,
        test_hypotheses,
        walk_forward_abc,
    )
    from bot.research.mtf.context_builder import load_corrected_trade_contexts
    from bot.research.mtf.data_audit import audit_data_coverage
    from bot.research.mtf.report import determine_verdict, render_report
    from bot.research.mtf.snapshots import ensure_tables
    from bot.research.mtf.timeframe_research import timeframe_research_status

    init_db()
    with connect() as conn:
        ensure_tables(conn)
        audit = audit_data_coverage(conn)
        contexts = load_corrected_trade_contexts(conn)
        htf_matrix = context_performance_matrix(contexts, "htf_label")
        align_matrix = context_performance_matrix(contexts, "alignment_label")
        hypotheses = test_hypotheses(contexts)
        wf = walk_forward_abc(contexts)
        predictive = predictive_value_by_timeframe(contexts)
        tf_research = timeframe_research_status()
        verdict = determine_verdict(audit, wf, hypotheses, predictive, len(contexts))
        report = render_report(
            audit, htf_matrix, align_matrix, hypotheses, wf, predictive,
            tf_research, len(contexts), verdict,
        )
    return {"report": report, "verdict": verdict, "trade_count": len(contexts)}


def run_diagnose_discovery() -> int:
    from bot.research.mtf.discovery import diagnose_discovery, render_diagnose_discovery

    diagnostics = diagnose_discovery()
    print(render_diagnose_discovery(diagnostics))
    return 0


def run_audit_production(*, offline: bool = True, progress: bool = True) -> int:
    from bot.database import connect, init_db
    from bot.research.mtf.integrity_audit import audit_production, render_production_audit

    init_db()
    with connect() as conn:
        report = audit_production(conn, offline=offline, progress=progress)
        print(render_production_audit(report))
    return 0


def run_research_15m() -> int:
    from bot.database import connect, init_db
    from bot.research.mtf.research_15m.engine import run_15m_research
    from bot.research.mtf.research_15m.report import render_15m_report

    init_db()
    with connect() as conn:
        result = run_15m_research(conn)
        print(render_15m_report(result))
    return 0


def run_research_1h() -> int:
    from bot.database import connect, init_db
    from bot.research.mtf.research_1h.engine import run_1h_research
    from bot.research.mtf.research_1h.report import render_1h_report

    init_db()
    with connect() as conn:
        result = run_1h_research(conn)
        print(render_1h_report(result))
    return 0


def run_research_daily() -> int:
    from bot.database import connect, init_db
    from bot.research.mtf.research_daily.engine import run_daily_research
    from bot.research.mtf.research_daily.report import render_daily_report

    init_db()
    with connect() as conn:
        result = run_daily_research(conn)
        print(render_daily_report(result))
    return 0


def run_research_15m_compare() -> int:
    import json
    from bot.database import connect, init_db
    from bot.research.mtf.clean_eligibility import audit_bid_gt_ask_timeline, count_clean_vs_raw
    from bot.research.mtf.research_15m.engine import run_15m_raw_vs_clean
    from bot.research.mtf.snapshots import ensure_tables

    init_db()
    with connect() as conn:
        ensure_tables(conn)
        counts = count_clean_vs_raw(conn)
        bid_audit = audit_bid_gt_ask_timeline(conn)
        compare = run_15m_raw_vs_clean(conn)
    print("MTF 15m RAW vs CLEAN COMPARISON")
    print(f"Rows: raw={counts['raw_rows']} clean={counts['clean_rows']} rejected={counts['rejected']}")
    print(f"BID_GT_ASK: total={bid_audit['total_bid_gt_ask']} before_fix={bid_audit['before_fix']} after_fix={bid_audit['after_fix']}")
    print(f"Stale token hypothesis: {bid_audit['stale_token_hypothesis']}")
    print(json.dumps(compare, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MTF Polymarket context research")
    parser.add_argument("--audit-only", action="store_true", help="Data audit section only")
    parser.add_argument(
        "--offline",
        action="store_true",
        default=True,
        help="audit-production: local DB only, no network (default)",
    )
    parser.add_argument(
        "--no-offline",
        action="store_false",
        dest="offline",
        help="audit-production: allow online checks (discovery still uses bounded HTTP)",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        default=True,
        help="audit-production: print stage progress to stderr (default)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_false",
        dest="progress",
        help="audit-production: suppress progress logging",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="research",
        choices=("research", "diagnose-discovery", "audit-production", "research-15m", "research-15m-compare", "research-1h", "research-daily"),
        help="Subcommand (default: research)",
    )
    args = parser.parse_args()

    if args.command == "diagnose-discovery":
        return run_diagnose_discovery()

    if args.command == "audit-production":
        return run_audit_production(offline=args.offline, progress=args.progress)

    if args.command == "research-15m":
        return run_research_15m()

    if args.command == "research-15m-compare":
        return run_research_15m_compare()

    if args.command == "research-1h":
        return run_research_1h()

    if args.command == "research-daily":
        return run_research_daily()

    if args.audit_only:
        from bot.database import connect, init_db
        from bot.research.mtf.data_audit import audit_data_coverage
        from bot.research.mtf.snapshots import ensure_tables
        import json

        init_db()
        with connect() as conn:
            ensure_tables(conn)
            audit = audit_data_coverage(conn)
            print(json.dumps(audit, indent=2, default=str))
        return 0

    result = run_research()
    print(result["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
