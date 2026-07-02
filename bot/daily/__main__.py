"""CLI: python -m bot.daily — full daily compute pipeline."""

from __future__ import annotations

import argparse
import sys

from bot.daily.pipeline import run_daily_pipeline
from bot.database import connect, init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trading AI daily compute pipeline")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Sampled optimizer grid (faster)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Skip writing report files",
    )
    args = parser.parse_args(argv)

    init_db()
    with connect() as conn:
        summary = run_daily_pipeline(
            conn,
            quick_optimizer=args.quick,
            save_files=not args.no_save,
        )

    print("\nDaily pipeline complete.")
    print(f"Features synced: {summary.get('features_synced', 0)}")
    if paths := summary.get("report_paths"):
        print(f"Report: {paths[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
