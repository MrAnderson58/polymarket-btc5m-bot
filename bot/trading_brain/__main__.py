"""CLI: python -m bot.trading_brain"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bot.config import BASE_DIR
from bot.database import connect, init_db
from bot.trading_brain.learning import run_brain_learning


def main(argv: list[str] | None = None) -> int:
    del argv
    init_db()
    with connect() as conn:
        summary = run_brain_learning(conn)
        conn.commit()

    out_dir = BASE_DIR / "trading_brain_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
    path = out_dir / f"brain_{stamp}.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Trading Brain sync: {summary['trades_processed']} trades")
    print(f"Contexts: {summary['contexts_built']} | Knowledge links: {summary['knowledge_links']}")
    print(f"New since last run: {summary['new_trades_since_last']}")
    print(f"Report: {path}")
    print("\nObserve-only — no trading impact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
