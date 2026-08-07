"""ai-server-health — unified Mac mini AI-server status."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.ops.server_infra_v1.config import (
    AI_SERVER_SERVICES,
    BACKUP_ROOT,
    CPU_WARN_LOAD,
    DISK_WARN_PCT,
    INFRA_AGENTS,
    LAUNCH_AGENTS,
    RAM_WARN_PCT,
    REPO,
    TRAVEL_AI_ROOT,
)
from bot.ops.server_infra_v1.launchd_mgr import audit_launchd, launchctl_loaded_labels
from bot.research.market_events.config import MARKET_EVENTS_DATABASE_PATH


@dataclass
class Check:
    name: str
    status: str  # PASS / FAIL / WARN
    detail: str = ""


@dataclass
class HealthBundle:
    checks: list[Check] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)


def _run(cmd: list[str], *, timeout: float = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except Exception as exc:
        return 1, str(exc)


def check_ssh() -> Check:
    # Port listening
    sock_ok = False
    try:
        with socket.create_connection(("127.0.0.1", 22), timeout=1.5):
            sock_ok = True
    except OSError:
        sock_ok = False

    # Key auth artifacts
    auth_keys = Path.home() / ".ssh" / "authorized_keys"
    keys_ok = auth_keys.exists() and auth_keys.stat().st_size > 0

    # BatchMode probe (no password prompt)
    code, out = _run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=3",
            "-o", "PreferredAuthentications=publickey",
            "localhost",
            "echo",
            "SSH_OK",
        ],
        timeout=8,
    )
    batch_ok = code == 0 and "SSH_OK" in out

    if batch_ok and keys_ok:
        return Check("SSH", "PASS", "key auth BatchMode=yes localhost OK")
    if keys_ok and not sock_ok:
        # Keys ready; daemon requires Remote Login (needs admin once).
        return Check(
            "SSH",
            "WARN",
            "authorized_keys ready; enable Remote Login "
            "(System Settings → General → Sharing → Remote Login) then re-check",
        )
    if sock_ok and keys_ok:
        return Check(
            "SSH",
            "WARN",
            "sshd listening + authorized_keys present; BatchMode probe failed "
            f"(code={code})",
        )
    return Check("SSH", "FAIL", f"key/BatchMode failed sock={sock_ok} keys={keys_ok}")


def check_tailscale() -> Check:
    if not shutil.which("tailscale"):
        return Check("Tailscale", "FAIL", "tailscale binary not found")
    code, out = _run(["tailscale", "status"], timeout=20)
    if code != 0:
        return Check("Tailscale", "FAIL", out[:200] or f"exit {code}")
    low = out.lower()
    if "stopped" in low or "logged out" in low or "needs login" in low:
        return Check("Tailscale", "FAIL", out[:200])
    # autostart hint
    return Check("Tailscale", "PASS", "tailscale status OK")


def check_service(svc: dict[str, str], loaded: set[str]) -> Check:
    title = svc["title"]
    label = svc["label"]
    installed = (LAUNCH_AGENTS / f"{label}.plist").exists()
    is_loaded = label in loaded

    if svc["key"] == "travel":
        travel_enabled = os.environ.get("AI_SERVER_TRAVEL_ENABLED", "1") != "0"
        if not travel_enabled:
            return Check(title, "PASS", "disabled via AI_SERVER_TRAVEL_ENABLED=0")
        if not TRAVEL_AI_ROOT.exists():
            # template must still be installed for reboot autonomy
            if installed and is_loaded:
                return Check(
                    title,
                    "WARN",
                    f"launchd loaded; TRAVEL_AI_ROOT missing ({TRAVEL_AI_ROOT})",
                )
            return Check(
                title,
                "WARN",
                f"TRAVEL_AI_ROOT missing ({TRAVEL_AI_ROOT}); set path or disable",
            )

    if svc["schedule"] == "daily":
        # Hermes: PASS if template+agent installed (not always running)
        if installed and is_loaded:
            return Check(title, "PASS", f"scheduled launchd {label}")
        if installed:
            return Check(title, "WARN", f"installed but not loaded: {label}")
        return Check(title, "FAIL", f"launchd missing: {label}")

    if installed and is_loaded:
        return Check(title, "PASS", f"KeepAlive {label}")
    if installed:
        return Check(title, "WARN", f"installed but not loaded: {label}")
    return Check(title, "FAIL", f"launchd missing: {label}")


def check_disk() -> tuple[Check, dict[str, Any]]:
    usage = shutil.disk_usage(str(REPO))
    pct = 100.0 * usage.used / max(1, usage.total)
    meta = {
        "total_gb": round(usage.total / 1e9, 2),
        "used_gb": round(usage.used / 1e9, 2),
        "free_gb": round(usage.free / 1e9, 2),
        "used_pct": round(pct, 1),
    }
    if pct >= DISK_WARN_PCT:
        return Check("Disk", "FAIL", f"{pct:.1f}% used"), meta
    if pct >= DISK_WARN_PCT - 10:
        return Check("Disk", "WARN", f"{pct:.1f}% used"), meta
    return Check("Disk", "PASS", f"{pct:.1f}% used"), meta


def check_ram() -> tuple[Check, dict[str, Any]]:
    # macOS: vm_stat
    code, out = _run(["vm_stat"], timeout=5)
    meta: dict[str, Any] = {"raw_head": out[:200]}
    if code != 0:
        return Check("RAM", "WARN", "vm_stat unavailable"), meta
    page_size = 4096
    free = inactive = speculative = 0
    for line in out.splitlines():
        if "page size of" in line:
            try:
                page_size = int(line.split()[-2])
            except Exception:
                pass
        if line.startswith("Pages free:"):
            free = int(line.split(":")[1].strip().rstrip("."))
        if line.startswith("Pages inactive:"):
            inactive = int(line.split(":")[1].strip().rstrip("."))
        if line.startswith("Pages speculative:"):
            speculative = int(line.split(":")[1].strip().rstrip("."))
    # total from sysctl
    code2, tot = _run(["sysctl", "-n", "hw.memsize"], timeout=5)
    total = int(tot) if code2 == 0 and tot.isdigit() else 0
    avail = (free + inactive + speculative) * page_size
    used_pct = 100.0 * (1.0 - (avail / total)) if total else 0.0
    meta.update({"total_gb": round(total / 1e9, 2), "used_pct": round(used_pct, 1)})
    if used_pct >= RAM_WARN_PCT:
        return Check("RAM", "FAIL", f"{used_pct:.1f}% used"), meta
    return Check("RAM", "PASS", f"{used_pct:.1f}% used"), meta


def check_cpu() -> tuple[Check, dict[str, Any]]:
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError as exc:
        return Check("CPU", "WARN", f"loadavg unavailable: {exc}"), {}
    meta = {"load1": load1, "load5": load5, "load15": load15}
    if load1 >= CPU_WARN_LOAD:
        return Check("CPU", "WARN", f"load1={load1:.2f}"), meta
    return Check("CPU", "PASS", f"load1={load1:.2f}"), meta


def check_database() -> Check:
    path = Path(MARKET_EVENTS_DATABASE_PATH)
    if not path.exists():
        # try default trades.db
        alt = REPO / "data" / "trades.db"
        path = alt if alt.exists() else path
    if not path.exists():
        return Check("Database", "FAIL", f"missing {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            ok = row and str(row[0]).lower() == "ok"
        finally:
            conn.close()
        if ok:
            return Check("Database", "PASS", f"quick_check ok ({path.name})")
        return Check("Database", "FAIL", f"quick_check={row}")
    except Exception as exc:
        return Check("Database", "FAIL", str(exc)[:160])


def check_queues() -> Check:
    pid_dir = REPO / "data" / "market_events_supervisor"
    if not pid_dir.exists():
        return Check("Queues", "WARN", "supervisor pid dir missing (optional)")
    n = len(list(pid_dir.glob("*.pid")))
    return Check("Queues", "PASS", f"supervisor pids={n}")


def check_launchd_block() -> Check:
    audit = audit_launchd()
    if audit["ok"]:
        return Check("Launchd", "PASS", f"all {len(audit['rows'])} agents loaded")
    missing = audit.get("missing") or []
    return Check(
        "Launchd",
        "FAIL",
        f"missing/not loaded: {', '.join(missing[:8])}"
        + ("…" if len(missing) > 8 else ""),
    )


def check_backups() -> Check:
    if not BACKUP_ROOT.exists():
        return Check("Backups", "WARN", "no backups yet (run ai-server-backup)")
    archives = sorted(BACKUP_ROOT.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime)
    if not archives:
        return Check("Backups", "WARN", "backup dir empty")
    latest = archives[-1]
    age_h = (time.time() - latest.stat().st_mtime) / 3600.0
    if age_h > 36:
        return Check("Backups", "FAIL", f"latest {latest.name} age={age_h:.1f}h")
    return Check("Backups", "PASS", f"latest {latest.name} age={age_h:.1f}h")


def check_watchdog_agent() -> Check:
    label = "com.polymarket.ai-server-watchdog"
    loaded = label in launchctl_loaded_labels()
    installed = (LAUNCH_AGENTS / f"{label}.plist").exists()
    if installed and loaded:
        return Check("Watchdog", "PASS", "interval agent loaded")
    if installed:
        return Check("Watchdog", "WARN", "installed but not loaded")
    return Check("Watchdog", "FAIL", "watchdog launchd missing")


def run_ai_server_health(*, include_watchdog: bool = True) -> dict[str, Any]:
    loaded = launchctl_loaded_labels()
    bundle = HealthBundle()

    bundle.checks.append(check_ssh())
    bundle.checks.append(check_tailscale())

    for svc in AI_SERVER_SERVICES:
        bundle.checks.append(check_service(svc, loaded))

    disk_c, disk_m = check_disk()
    ram_c, ram_m = check_ram()
    cpu_c, cpu_m = check_cpu()
    bundle.checks.extend([disk_c, ram_c, cpu_c])
    bundle.metrics.update({"disk": disk_m, "ram": ram_m, "cpu": cpu_m})

    bundle.checks.append(check_database())
    bundle.checks.append(check_queues())
    bundle.checks.append(check_launchd_block())
    bundle.checks.append(check_backups())
    if include_watchdog:
        bundle.checks.append(check_watchdog_agent())

    lines = ["AI SERVER HEALTH V1", ""]
    for c in bundle.checks:
        lines.append(f"{c.name}")
        lines.append(c.status)
        if c.detail:
            lines.append(c.detail)
        lines.append("")
    lines.append(f"overall={'PASS' if bundle.ok else 'FAIL'}")
    lines.append("")
    terminal = "\n".join(lines)
    return {
        "ok": bundle.ok,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in bundle.checks],
        "metrics": bundle.metrics,
        "terminal": terminal,
        "json": {
            "ok": bundle.ok,
            "checks": {c.name: c.status for c in bundle.checks},
            "metrics": bundle.metrics,
        },
    }


def format_health_json(result: dict[str, Any]) -> str:
    return json.dumps(result.get("json") or result, indent=2, default=str)
