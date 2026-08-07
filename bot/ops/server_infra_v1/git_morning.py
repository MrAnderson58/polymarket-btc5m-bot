"""Morning git report — status/fetch/HEAD/branch only (no pull/push)."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.ops.server_infra_v1.config import REPO


def _git(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as exc:
        return 1, str(exc)


def run_git_morning() -> dict[str, Any]:
    t0 = time.time()
    code_status, status = _git("status", "--short", "--branch")
    code_fetch, fetch = _git("fetch", "--dry-run")
    # real fetch (network) — still no pull/push
    code_fetch2, fetch2 = _git("fetch", "--prune")
    code_head, head = _git("rev-parse", "HEAD")
    code_branch, branch = _git("branch", "--show-current")
    code_tracking, tracking = _git("status", "-sb")

    report = "\n".join([
        "# AI SERVER GIT MORNING REPORT",
        "",
        f"date_utc: {datetime.now(timezone.utc).isoformat()}",
        f"repo: {REPO}",
        "",
        "## branch",
        branch or "(unknown)",
        "",
        "## HEAD",
        head or "(unknown)",
        "",
        "## git status -sb",
        tracking or status or "(empty)",
        "",
        "## git fetch --prune",
        f"exit={code_fetch2}",
        fetch2[:2000] or "(ok, no output)",
        "",
        "## notes",
        "- No automatic pull",
        "- No automatic push",
        "",
    ])
    out_dir = REPO / "reports" / "ops"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "GIT_MORNING_REPORT.md"
    path.write_text(report, encoding="utf-8")
    root = REPO / "GIT_MORNING_REPORT.md"
    root.write_text(report, encoding="utf-8")
    elapsed = round(time.time() - t0, 3)
    terminal = "\n".join([
        "AI SERVER GIT MORNING V1",
        "",
        f"branch={branch} HEAD={head[:12] if head else '?'}",
        f"status_exit={code_status} fetch_exit={code_fetch2}",
        f"report={path}",
        f"elapsed={elapsed}s",
        "no_pull=true no_push=true",
        "",
    ])
    return {
        "ok": code_head == 0 and code_branch == 0,
        "branch": branch,
        "head": head,
        "status": tracking or status,
        "fetch_exit": code_fetch2,
        "paths": {"root": str(root), "out_dir": str(path)},
        "elapsed_sec": elapsed,
        "terminal": terminal,
    }
