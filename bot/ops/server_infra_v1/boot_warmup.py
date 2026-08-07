"""Boot warmup — wait 2–3 minutes after load then kickstart core KeepAlive services."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

from bot.ops.server_infra_v1.config import AI_SERVER_SERVICES, REPO
from bot.ops.server_infra_v1.launchd_mgr import write_all_templates
from bot.ops.server_infra_v1.watchdog import run_watchdog


def run_boot_warmup(*, delay_sec: float | None = None, skip_sleep: bool = False) -> dict[str, Any]:
    delay = 150.0 if delay_sec is None else float(delay_sec)  # ~2.5 minutes
    t0 = time.time()
    if not skip_sleep and delay > 0:
        time.sleep(delay)
    write_all_templates()
    wd = run_watchdog(dry_run=False)
    uid = os.getuid()
    for svc in AI_SERVER_SERVICES:
        label = svc["label"]
        subprocess.run(
            ["launchctl", "kickstart", f"gui/{uid}/{label}"],
            check=False,
            capture_output=True,
        )
    elapsed = round(time.time() - t0, 3)
    log = REPO / "logs" / "ai-server-boot-warmup.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.open("a", encoding="utf-8").write(f"{int(time.time())} warmup ok elapsed={elapsed}\n")
    terminal = "\n".join([
        "AI SERVER BOOT WARMUP V1",
        "",
        f"delay_sec={delay} elapsed={elapsed}s",
        f"watchdog_ok={wd.get('ok')} restarted={wd.get('restarted')}",
        "",
    ])
    return {"ok": True, "elapsed_sec": elapsed, "watchdog": wd, "terminal": terminal}
