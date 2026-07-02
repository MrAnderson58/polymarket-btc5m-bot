"""CLI: python -m bot.scientist"""

from __future__ import annotations

import sys

from bot.database import connect, init_db
from bot.scientist.builder import build_scientist_section
from bot.scientist.render import write_scientist_report


def main(argv: list[str] | None = None) -> int:
    del argv
    init_db()
    with connect() as conn:
        report = build_scientist_section(conn, run_cycle=True)

    md_path, json_path = write_scientist_report(report)
    summary = report.get("summary", {})
    best = report.get("best_next_step", {})

    print(f"AI Scientist v{summary.get('version', '?')}")
    print(f"Patterns: {summary.get('patterns_found', 0)} | New hypotheses: {summary.get('new_hypotheses', 0)}")
    print(f"Passed: {summary.get('passed', 0)} | Failed/rejected: {summary.get('failed', 0)}")
    if best.get("blocked"):
        print(f"\nBest next step: BLOCKED — {best.get('reason', '')[:80]}")
    else:
        print(f"\nBest next step: {best.get('recommendation')} (+{best.get('expected_pf_pct', 0):.0f}% PF)")
    print(f"\nReport: {md_path}")
    print(f"JSON:   {json_path}")
    print("\nObserve-only — human approval required for any experiment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
