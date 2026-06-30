"""CLI entry point: python -m bot.report"""

from __future__ import annotations

import sys

from bot.database import connect, init_db
from bot.report.builder import save_report


def main(argv: list[str] | None = None) -> int:
    del argv
    init_db()
    with connect() as conn:
        md_path, json_path, zip_path = save_report(conn)
    print(f"Report saved:\n  {md_path}\n  {json_path}\n  {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
