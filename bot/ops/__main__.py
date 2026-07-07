"""Operational CLI: python -m bot.ops healthcheck | snapshot | prod-control ..."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: python -m bot.ops healthcheck | snapshot | prod-control <status|start|stop|restart>")
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "healthcheck":
        from bot.ops.healthcheck import main as health_main

        return health_main()
    if cmd == "snapshot":
        from bot.ops.snapshot import main as snap_main

        return snap_main()
    if cmd == "prod-control":
        from bot.ops.prod_control import main as ctl_main

        return ctl_main(rest)
    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
