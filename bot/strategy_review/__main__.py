"""CLI: python -m bot.strategy_review"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bot.config import BASE_DIR
from bot.database import connect, init_db
from bot.report.builder import build_report
from bot.strategy_review.builder import build_strategy_review
from bot.strategy_review.render import render_strategy_review_section


def main(argv: list[str] | None = None) -> int:
    del argv
    init_db()
    with connect() as conn:
        from bot.analytics.live_sample import build_live_sample
        from bot.optimizer.cache import sections_for_report
        from bot.report.analytics import fetch_v2_trades, trade_pnl, _metrics
        from bot.report.builder import _build_configuration
        from bot.report.memory import load_experiments
        from bot.scientist.builder import build_scientist_section
        from bot.trading_brain.report import build_brain_report

        closed = fetch_v2_trades(conn, closed_only=True)
        partial = {
            "configuration": _build_configuration(),
            **sections_for_report(),
            "live_sample": build_live_sample(
                load_experiments(), _metrics([trade_pnl(t) for t in closed])["trades"]
            ),
            "scientist": build_scientist_section(conn, run_cycle=False),
            "trading_brain": build_brain_report(conn),
            "overfit_detector": {"level": "LOW", "overfit": False, "reasons": []},
        }
        review = build_strategy_review(conn, partial, read_only=False)

    out_dir = BASE_DIR / "strategy_review_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    json_path = out_dir / f"strategy_review_{stamp}.json"
    md_path = out_dir / f"strategy_review_{stamp}.md"

    json_path.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Strategy Review", ""] + render_strategy_review_section(review)
    md_path.write_text("\n".join(lines), encoding="utf-8")

    verdict = review.get("final_verdict", {})
    print(f"Strategy Review v{review.get('version')}")
    print(f"Decision: {verdict.get('decision')}")
    print(f"Confidence: {verdict.get('confidence_pct', 0):.0f}%")
    print(f"Reason: {verdict.get('reason', '')[:100]}")
    print(f"\nReport: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
