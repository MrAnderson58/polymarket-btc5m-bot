"""Reboot / reconnect simulation tests for Server Infra V1 (no real reboot)."""

from __future__ import annotations

import socket
import time
from typing import Any
from unittest import mock

from bot.ops.server_infra_v1 import health as health_mod
from bot.ops.server_infra_v1.backup import run_backup
from bot.ops.server_infra_v1.health import run_ai_server_health
from bot.ops.server_infra_v1.launchd_mgr import write_all_templates
from bot.ops.server_infra_v1.watchdog import run_watchdog


def simulate_reboot_recovery() -> dict[str, Any]:
    """Simulate post-reboot recovery path without rebooting the Mac."""
    t0 = time.time()
    results: dict[str, Any] = {}

    results["templates"] = write_all_templates()

    # SSH reconnect simulation — attempt localhost probe
    try:
        with socket.create_connection(("127.0.0.1", 22), timeout=1.5):
            results["ssh_reconnect"] = {"ok": True, "detail": "port open"}
    except OSError as exc:
        results["ssh_reconnect"] = {"ok": False, "detail": str(exc)}

    # Tailscale reconnect simulation
    code, out = health_mod._run(["tailscale", "status"], timeout=15)
    results["tailscale_reconnect"] = {"ok": code == 0, "detail": out[:200]}

    # launchd restart simulation (dry watchdog)
    results["launchd_restart"] = run_watchdog(dry_run=True)

    # watchdog restart simulation
    results["watchdog_restart"] = run_watchdog(dry_run=True)

    # dashboard / database via health slices
    with mock.patch.object(health_mod, "check_ssh", return_value=health_mod.Check("SSH", "PASS", "sim")):
        with mock.patch.object(
            health_mod, "check_tailscale", return_value=health_mod.Check("Tailscale", "PASS", "sim")
        ):
            # still run real disk/db where possible
            h = run_ai_server_health(include_watchdog=True)
    results["health"] = {
        "ok": h.get("ok"),
        "checks": {c["name"]: c["status"] for c in h.get("checks") or []},
    }
    results["database"] = next(
        (c for c in (h.get("checks") or []) if c["name"] == "Database"),
        {"status": "FAIL"},
    )
    results["dashboard"] = next(
        (c for c in (h.get("checks") or []) if c["name"] == "Dashboard"),
        {"status": "FAIL"},
    )

    # backup smoke (creates archive — proves backup path)
    try:
        results["backup"] = run_backup()
    except Exception as exc:
        results["backup"] = {"ok": False, "error": str(exc)}

    elapsed = round(time.time() - t0, 3)
    # Simulation PASS criteria: templates ok + watchdog dry ok + db not FAIL + backup ok
    sim_ok = bool(results["templates"].get("ok")) and bool(
        results["launchd_restart"].get("ok") is not False
    ) and results["database"].get("status") != "FAIL" and bool(
        (results.get("backup") or {}).get("ok")
    )
    terminal = "\n".join([
        "AI SERVER REBOOT SIMULATION V1",
        "",
        f"elapsed={elapsed}s sim_ok={sim_ok}",
        f"ssh_reconnect={results['ssh_reconnect']}",
        f"tailscale_reconnect={results['tailscale_reconnect'].get('ok')}",
        f"watchdog_dry_ok={results['watchdog_restart'].get('ok')}",
        f"database={results['database'].get('status')}",
        f"dashboard={results['dashboard'].get('status')}",
        f"backup_ok={(results.get('backup') or {}).get('ok')}",
        "",
        "NOTE: full PASS requires installed launchd agents + SSH Remote Login + Tailscale up.",
        "",
    ])
    return {"ok": sim_ok, "elapsed_sec": elapsed, "results": results, "terminal": terminal}
