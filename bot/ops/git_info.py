"""Git metadata for operational reports."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from bot.config import BASE_DIR


@dataclass(frozen=True)
class GitStatus:
    branch: str
    commit_sha: str
    dirty: bool
    dirty_files: list[str]


def read_git_status(repo: Path | None = None) -> GitStatus:
    root = repo or BASE_DIR
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        dirty_files = [line[3:] for line in status.splitlines() if line.strip()]
        return GitStatus(
            branch=branch,
            commit_sha=sha,
            dirty=bool(dirty_files),
            dirty_files=dirty_files,
        )
    except (subprocess.SubprocessError, OSError):
        return GitStatus(branch="unknown", commit_sha="unknown", dirty=False, dirty_files=[])
