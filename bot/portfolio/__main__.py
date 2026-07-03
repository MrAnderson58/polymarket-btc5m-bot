"""CLI: python -m bot.portfolio reset-guards.

Uses direct imports to avoid pulling in the heavy execution chain.
"""

from __future__ import annotations

import sys


def reset_guards() -> int:
    """Clear circuit breaker and trading_paused flags without touching history or journal."""
    from bot.database import connect, init_db
    from bot.portfolio.portfolio import PortfolioManager

    init_db()
    with connect() as conn:
        manager = PortfolioManager.load(conn)
        was_paused = manager.state.paused
        reason = manager.state.pause_reason

        manager.clear_pause(conn)
        conn.commit()

        if was_paused:
            print(f"Guards reset. Was: {reason}")
        else:
            print("Guards already clear — nothing to reset.")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m bot.portfolio <command>")
        print("")
        print("Commands:")
        print("  reset-guards   Clear circuit breaker and trading_paused")
        return 1

    cmd = sys.argv[1]
    if cmd == "reset-guards":
        return reset_guards()

    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
