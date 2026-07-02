"""CLI: python -m bot.live_check — READY / NOT READY."""

from __future__ import annotations

import sys

from bot.portfolio.live_check import main


if __name__ == "__main__":
    sys.exit(main())
