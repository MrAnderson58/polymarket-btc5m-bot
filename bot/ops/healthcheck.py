"""Production health check — read-only, no secrets."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from bot.config import (
    BASE_DIR,
    DATABASE_PATH,
    ENABLE_V4_SHADOW,
    MTF_POLL_INTERVAL_SEC,
    POLL_INTERVAL_SEC,
    V4_POLL_INTERVAL_SEC,
)
from bot.database import connect
from bot.ops.collector_health import analyze_collector_health
from bot.ops.git_info import read_git_status
from bot.ops.process_utils import (
    assess_telegram_lock,
    find_main_bot_processes,
    find_telegram_poll_processes,
    project_python,
)
from bot.ops.redact import env_configured, is_secret_env_name, redact_database_url
from bot.research.futures_agent.env_bootstrap import bootstrap_config, resolve_agent_db_config

REQUIRED_MAIN_ENV = (
    "DATABASE_PATH",
    "POLL_INTERVAL_SEC",
    "V4_POLL_INTERVAL_SEC",
    "MTF_POLL_INTERVAL_SEC",
    "ENABLE_V4_SHADOW",
)

REQUIRED_TELEGRAM_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_AGENT_ALLOWED_CHAT_IDS",
    "FUTURES_AGENT_DATABASE_URL",
)

OPTIONAL_ENV = (
    "FUTURES_SOURCE_DATABASE_URL",
    "FUTURES_AGENT_SQLITE_PATH",
    "TELEGRAM_AGENT_CHAT_ID",
    "TRADING_MODE",
    "LIVE_ENABLED",
    "POLY_PRIVATE_KEY",
)


@dataclass
class HealthReport:
    status: str = "HEALTHY"
    issues: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def _append_section(report: HealthReport, title: str) -> None:
    report.lines.append("")
    report.lines.append(title)
    report.lines.append("-" * len(title))


def _note_issue(report: HealthReport, issue: str, *, critical: bool = False) -> None:
    report.issues.append(issue)
    if critical:
        report.status = "CRITICAL"
    elif report.status == "HEALTHY":
        report.status = "DEGRADED"


def _check_postgres(url: str) -> tuple[bool, str]:
    if not url.startswith(("postgres://", "postgresql://")):
        return False, "not a postgres URL"
    try:
        import psycopg2

        conn = psycopg2.connect(url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            dbname = conn.info.dbname if hasattr(conn, "info") else "unknown"
            return True, dbname
        finally:
            conn.close()
    except Exception as exc:
        return False, type(exc).__name__


def _sqlite_quick_check(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "file missing"
    if not os.access(path, os.R_OK):
        return False, "not readable"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            ok = row is not None and row[0] == "ok"
            return ok, row[0] if row else "no result"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, type(exc).__name__


def _disk_free_gb(path: Path) -> float | None:
    try:
        usage = shutil.disk_usage(path)
        return usage.free / (1024**3)
    except OSError:
        return None


def _webhook_status_safe() -> str:
    try:
        from bot.research.futures_agent.telegram_inbound import run_diagnose

        report = run_diagnose()
        if report.get("webhook_active"):
            return "active (conflicts with polling)"
        return "inactive"
    except Exception as exc:
        return f"check_failed:{type(exc).__name__}"


def run_healthcheck() -> HealthReport:
    bootstrap_config()
    report = HealthReport()

    git = read_git_status()
    _append_section(report, "GIT")
    report.lines.append(f"branch: {git.branch}")
    report.lines.append(f"commit: {git.commit_sha}")
    report.lines.append(f"dirty working tree: {'yes' if git.dirty else 'no'}")
    if git.dirty:
        _note_issue(report, "git working tree is dirty")

    py = project_python()
    _append_section(report, "PYTHON")
    report.lines.append(f"executable: {py}")
    report.lines.append(f"version: {sys.version.split()[0]}")
    venv = BASE_DIR / ".venv"
    report.lines.append(f"virtualenv: {venv if venv.is_dir() else 'missing'}")

    main_procs = find_main_bot_processes()
    _append_section(report, "MAIN BOT")
    report.lines.append(f"running: {'yes' if main_procs else 'no'}")
    for proc in main_procs:
        report.lines.append(f"  PID {proc.pid}: {proc.command[:160]}")
    if len(main_procs) > 1:
        _note_issue(report, f"duplicate bot.main processes: {len(main_procs)}", critical=True)
    if not main_procs:
        _note_issue(report, "bot.main is not running", critical=True)

    tg_procs = find_telegram_poll_processes()
    lock_status = assess_telegram_lock()
    _append_section(report, "TELEGRAM POLLER")
    report.lines.append(f"running: {'yes' if tg_procs else 'no'}")
    for proc in tg_procs:
        report.lines.append(f"  PID {proc.pid}: {proc.command[:160]}")
    if len(tg_procs) > 1:
        _note_issue(report, f"duplicate telegram-poll processes: {len(tg_procs)}", critical=True)
    report.lines.append(f"webhook status: {_webhook_status_safe()}")
    report.lines.append(f"polling lock: {lock_status}")
    if lock_status == "stale_lock_available" and not tg_procs:
        _note_issue(report, "stale telegram poll lock file without running process")

    _append_section(report, "DATABASES")
    sqlite_ok, sqlite_detail = _sqlite_quick_check(DATABASE_PATH)
    report.lines.append(f"SQLite path: {DATABASE_PATH}")
    report.lines.append(f"SQLite readable/quick_check: {'yes' if sqlite_ok else 'no'} ({sqlite_detail})")
    if not sqlite_ok:
        _note_issue(report, f"SQLite unhealthy: {sqlite_detail}", critical=True)
    try:
        size_mb = DATABASE_PATH.stat().st_size / (1024 * 1024) if DATABASE_PATH.exists() else 0
        report.lines.append(f"SQLite size: {size_mb:.1f} MB")
    except OSError:
        pass

    agent_cfg = resolve_agent_db_config()
    report.lines.append(f"Futures agent backend: {agent_cfg.backend}")
    if agent_cfg.database_name:
        report.lines.append(f"Futures agent database: {agent_cfg.database_name}")
    if agent_cfg.is_postgres:
        pg_ok, pg_detail = _check_postgres(agent_cfg.url)
        report.lines.append(f"PostgreSQL reachable: {'yes' if pg_ok else 'no'} ({pg_detail})")
        if not pg_ok:
            _note_issue(report, f"PostgreSQL unreachable: {pg_detail}", critical=True)
    elif agent_cfg.sqlite_path:
        sp = Path(agent_cfg.sqlite_path)
        ok, detail = _sqlite_quick_check(sp)
        report.lines.append(f"Agent SQLite: {sp} ({'ok' if ok else detail})")

    _append_section(report, "COLLECTOR HEALTH")
    if ENABLE_V4_SHADOW:
        try:
            with connect() as conn:
                collector = analyze_collector_health(conn, completed_limit=5)
            if collector.last_observation_ts:
                report.lines.append(f"last v4 observation ts: {collector.last_observation_ts}")
                report.lines.append(f"age seconds: {collector.last_observation_age_sec}")
            else:
                report.lines.append("last v4 observation: none")
                _note_issue(report, "no v4_shadow_observations rows")
            report.lines.append(
                f"completed markets analyzed: {len(collector.completed_markets)}"
            )
            if collector.median_obs_per_market is not None:
                report.lines.append(
                    f"median obs/market (completed): {collector.median_obs_per_market:.0f}"
                )
            if collector.median_gap_sec is not None:
                report.lines.append(
                    f"median gap sec (completed): {collector.median_gap_sec:.1f}"
                )
            for slug_stats in collector.completed_markets[-5:]:
                report.lines.append(
                    f"  {slug_stats.market_slug}: obs={slug_stats.obs_count} "
                    f"gap={slug_stats.median_gap_sec:.1f}s span={slug_stats.collector_span_sec}s"
                )
            for warning in collector.warnings:
                critical = "age" in warning and collector.last_observation_age_sec and collector.last_observation_age_sec > 300
                _note_issue(report, warning, critical=critical)
        except sqlite3.Error as exc:
            _note_issue(report, f"collector health query failed: {type(exc).__name__}", critical=True)
    else:
        report.lines.append("ENABLE_V4_SHADOW=false (collector metrics skipped)")

    _append_section(report, "CONFIG")
    report.lines.append("required main env:")
    for name in REQUIRED_MAIN_ENV:
        configured = env_configured(name)
        if name == "ENABLE_V4_SHADOW":
            report.lines.append(f"  {name}: configured={configured} effective={ENABLE_V4_SHADOW}")
        elif name == "POLL_INTERVAL_SEC":
            report.lines.append(f"  {name}: configured={configured} effective={POLL_INTERVAL_SEC}")
        elif name == "V4_POLL_INTERVAL_SEC":
            report.lines.append(f"  {name}: configured={configured} effective={V4_POLL_INTERVAL_SEC}")
        elif name == "MTF_POLL_INTERVAL_SEC":
            report.lines.append(f"  {name}: configured={configured} effective={MTF_POLL_INTERVAL_SEC}")
        elif name == "DATABASE_PATH":
            report.lines.append(f"  {name}: configured={configured} effective={DATABASE_PATH}")
        else:
            report.lines.append(f"  {name}: configured={configured}")
    report.lines.append("required telegram env:")
    for name in REQUIRED_TELEGRAM_ENV:
        report.lines.append(f"  {name}: configured={env_configured(name)}")
    report.lines.append("optional env (configured yes/no only):")
    for name in OPTIONAL_ENV:
        if is_secret_env_name(name):
            report.lines.append(f"  {name}: configured={env_configured(name)}")
        else:
            report.lines.append(f"  {name}: configured={env_configured(name)}")
    agent_url = os.getenv("FUTURES_AGENT_DATABASE_URL", "")
    if agent_url:
        report.lines.append(
            f"  FUTURES_AGENT_DATABASE_URL host: {redact_database_url(agent_url)}"
        )

    _append_section(report, "DISK")
    free_gb = _disk_free_gb(BASE_DIR)
    if free_gb is not None:
        report.lines.append(f"free disk space: {free_gb:.1f} GB")
        if free_gb < 2:
            _note_issue(report, f"low disk space: {free_gb:.1f} GB free", critical=True)
        elif free_gb < 5:
            _note_issue(report, f"disk space warning: {free_gb:.1f} GB free")

    report.lines.append("")
    report.lines.append(f"FINAL STATUS: {report.status}")
    if report.issues:
        report.lines.append("issues:")
        for issue in report.issues:
            report.lines.append(f"  - {issue}")
    return report


def print_healthcheck() -> int:
    report = run_healthcheck()
    print("\n".join(report.lines))
    if report.status == "CRITICAL":
        return 2
    if report.status == "DEGRADED":
        return 1
    return 0


def main() -> int:
    return print_healthcheck()


if __name__ == "__main__":
    raise SystemExit(main())
