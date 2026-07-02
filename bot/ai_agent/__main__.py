"""CLI: python -m bot.ai_agent"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bot.config import BASE_DIR
from bot.database import connect, init_db
from bot.ai_agent.daily import run_daily_sync


def main(argv: list[str] | None = None) -> int:
    del argv
    init_db()
    with connect() as conn:
        count, report = run_daily_sync(conn)

    out_dir = BASE_DIR / "ai_agent_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
    path = out_dir / f"ai_agent_{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"AI Agent sync: {count} signals")
    dec = report["decisions"]
    intel = report.get("intelligence", {})
    print(f"ALLOW {dec['ALLOW']} | SKIP {dec['SKIP']} | SHADOW {dec['SHADOW']}")
    print(
        f"Avg Score {intel.get('average_ai_score', 0)} | "
        f"Avg Confidence {intel.get('average_confidence', 0)}%"
    )
    print(f"Report: {path}")
    print("\nObserve-only mode — no trading impact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
