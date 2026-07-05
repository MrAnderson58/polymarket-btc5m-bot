"""Multi-Timeframe Polymarket Context Research Layer.

Usage:
  python -m bot.research.mtf

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


def main() -> int:
    parser = argparse.ArgumentParser(description="MTF Polymarket context research")
    parser.add_argument("--audit-only", action="store_true", help="Data audit section only")
    args = parser.parse_args()

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
