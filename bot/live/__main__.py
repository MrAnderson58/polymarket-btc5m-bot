"""CLI: python -m bot.live — live dashboard."""

from __future__ import annotations

import sys

from bot.database import connect, init_db
from bot.portfolio.dashboard import build_dashboard, format_dashboard


def main(argv: list[str] | None = None) -> int:
    del argv
    init_db()
    with connect() as conn:
        data = build_dashboard(conn)
        print(format_dashboard(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
