"""Server Infrastructure V1 CLI: python -m bot.ops.server_infra_v1 <cmd>."""

from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(
            "Usage: python -m bot.ops.server_infra_v1 "
            "{health|ai-server-health|backup|watchdog|git-morning|boot-warmup|"
            "install-launchd|audit-launchd|write-templates|reboot-sim}"
        )
        return 0
    cmd = argv[0]
    if cmd in {"health", "ai-server-health"}:
        from bot.ops.server_infra_v1.health import format_health_json, run_ai_server_health

        out = run_ai_server_health()
        print(out.get("terminal") or "")
        if "--json" in argv:
            print(format_health_json(out))
        return 0 if out.get("ok") else 1
    if cmd == "backup":
        from bot.ops.server_infra_v1.backup import run_backup

        out = run_backup()
        print(out.get("terminal") or "")
        return 0 if out.get("ok") else 1
    if cmd == "watchdog":
        from bot.ops.server_infra_v1.watchdog import run_watchdog

        dry = "--dry-run" in argv
        out = run_watchdog(dry_run=dry)
        print(out.get("terminal") or "")
        return 0 if out.get("ok") else 1
    if cmd == "git-morning":
        from bot.ops.server_infra_v1.git_morning import run_git_morning

        out = run_git_morning()
        print(out.get("terminal") or "")
        return 0 if out.get("ok") else 1
    if cmd == "boot-warmup":
        from bot.ops.server_infra_v1.boot_warmup import run_boot_warmup

        skip = "--skip-sleep" in argv
        out = run_boot_warmup(skip_sleep=skip, delay_sec=0 if skip else None)
        print(out.get("terminal") or "")
        return 0 if out.get("ok") else 1
    if cmd == "write-templates":
        from bot.ops.server_infra_v1.launchd_mgr import write_all_templates

        out = write_all_templates()
        print(json.dumps(out, indent=2))
        return 0
    if cmd == "audit-launchd":
        from bot.ops.server_infra_v1.launchd_mgr import audit_launchd

        out = audit_launchd()
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if cmd == "install-launchd":
        from bot.ops.server_infra_v1.launchd_mgr import install_launch_agents

        out = install_launch_agents(bootstrap=True)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if cmd == "reboot-sim":
        from bot.ops.server_infra_v1.reboot_sim import simulate_reboot_recovery

        out = simulate_reboot_recovery()
        print(out.get("terminal") or "")
        return 0 if out.get("ok") else 1
    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
