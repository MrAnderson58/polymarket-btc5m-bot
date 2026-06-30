"""CLI: python -m bot.optimizer"""

from __future__ import annotations

import argparse
import sys

from bot.database import connect, init_db
from bot.optimizer.builder import save_optimizer_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trading AI Optimizer v1 (read-only)")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Sample 5000 grid combos instead of full exhaustive search",
    )
    args = parser.parse_args(argv)

    init_db()
    with connect() as conn:
        md_path, json_path = save_optimizer_report(conn, full_grid=not args.quick)
    print(f"Optimizer report saved:\n  {md_path}\n  {json_path}")
    print("\nNo trading parameters were changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
