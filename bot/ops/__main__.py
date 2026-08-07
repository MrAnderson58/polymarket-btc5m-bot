"""Operational CLI: python -m bot.ops healthcheck | snapshot | prod-control ..."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(
            "Usage: python -m bot.ops healthcheck | snapshot | prod-control "
            "| ai-server-health | ai-server-backup | ai-server-watchdog"
        )
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
    if cmd in {
        "ai-server-health",
        "ai-server-backup",
        "ai-server-watchdog",
        "ai-server-git-morning",
        "ai-server-install-launchd",
        "ai-server-reboot-sim",
    }:
        from bot.ops.server_infra_v1.__main__ import main as infra_main

        mapped = {
            "ai-server-health": "health",
            "ai-server-backup": "backup",
            "ai-server-watchdog": "watchdog",
            "ai-server-git-morning": "git-morning",
            "ai-server-install-launchd": "install-launchd",
            "ai-server-reboot-sim": "reboot-sim",
        }
        return infra_main([mapped[cmd], *rest])
    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
