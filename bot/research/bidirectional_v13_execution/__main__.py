"""Bidirectional V1.3 Execution-Aware Research Layer.

Usage:
  python -m bot.research.bidirectional_v13_execution

Read-only. Does not modify live execution, V1.1, V1.2 shadow, ER, or MTF collectors.
"""

from __future__ import annotations

import argparse


def main() -> int:
    from bot.database import connect, init_db
    from bot.research.bidirectional_v13_execution.engine import run_v13_execution_research
    from bot.research.bidirectional_v13_execution.report import render_report
    from bot.strategy.bidirectional_shadow import ensure_tables

    parser = argparse.ArgumentParser(description="V1.3 execution-aware research (read-only)")
    parser.parse_args()

    init_db()
    with connect() as conn:
        ensure_tables(conn)
        report = run_v13_execution_research(conn)
    print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
