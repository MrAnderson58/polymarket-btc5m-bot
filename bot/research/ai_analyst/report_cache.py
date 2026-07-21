"""S46.4 — report file cache (mtime TTL, no shell)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from bot.research.ai_analyst.config import REPORTS_DIR, REPORT_ARTIFACTS, TelegramTerminalSettings


def report_path(key: str, *, reports_dir: Path | None = None) -> Path:
    name = REPORT_ARTIFACTS.get(key)
    if not name:
        raise KeyError(f"unknown report key: {key}")
    return (reports_dir or REPORTS_DIR) / name


def file_age_minutes(path: Path) -> float | None:
    if not path.is_file():
        return None
    return (time.time() - path.stat().st_mtime) / 60.0


def is_report_fresh(
    key: str,
    *,
    ttl_minutes: int,
    reports_dir: Path | None = None,
) -> bool:
    path = report_path(key, reports_dir=reports_dir)
    age = file_age_minutes(path)
    return age is not None and age <= ttl_minutes


def cache_status(
    *,
    ttl_minutes: int,
    reports_dir: Path | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ttl_minutes": ttl_minutes, "artifacts": {}}
    for key in ("market", "btc", "macro", "sp500", "telegram", "context"):
        path = report_path(key, reports_dir=reports_dir)
        age = file_age_minutes(path)
        out["artifacts"][key] = {
            "path": str(path),
            "exists": path.is_file(),
            "age_minutes": round(age, 2) if age is not None else None,
            "fresh": age is not None and age <= ttl_minutes,
        }
    return out


def ensure_fresh_report(
    key: str,
    *,
    ttl_minutes: int,
    reports_dir: Path | None = None,
    regenerate: Callable[[], dict[str, Any]],
) -> tuple[Path, bool]:
    """Return (path, regenerated). Calls regenerate() only when stale/missing."""
    path = report_path(key, reports_dir=reports_dir)
    if is_report_fresh(key, ttl_minutes=ttl_minutes, reports_dir=reports_dir):
        return path, False
    regenerate()
    return path, True


def ensure_full_report_suite(
    *,
    settings: TelegramTerminalSettings,
    reports_dir: Path | None = None,
    regenerate: Callable[[], dict[str, Any]],
) -> tuple[bool, dict[str, Any] | None]:
    """Regenerate full suite when market report stale. Returns (regenerated, result)."""
    if is_report_fresh(
        "market",
        ttl_minutes=settings.report_cache_minutes,
        reports_dir=reports_dir,
    ):
        return False, None
    result = regenerate()
    return True, result
