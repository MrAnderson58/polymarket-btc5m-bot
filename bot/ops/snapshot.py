"""Operational snapshot report — no secrets."""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from bot.config import BASE_DIR, DATABASE_PATH, ENABLE_V4_SHADOW
from bot.database import connect
from bot.ops.collector_health import analyze_collector_health
from bot.ops.git_info import read_git_status
from bot.ops.healthcheck import run_healthcheck
from bot.ops.process_utils import (
    assess_telegram_lock,
    find_main_bot_processes,
    find_telegram_poll_processes,
    project_python,
)
from bot.ops.redact import redact_text
from bot.research.futures_agent.env_bootstrap import bootstrap_config, resolve_agent_db_config

RESEARCH_TABLES = (
    "v4_shadow_observations",
    "v4_shadow_trades",
    "v4_collector_cycles",
    "main_collector_cycles",
    "bidirectional_shadow_observations",
    "bidirectional_shadow_trades",
    "ss_shadow_candidates",
    "ss_simulation_results",
    "ss_discovered_strategies",
    "futures_agent_inputs",
    "futures_agent_signals",
)


def _table_count(conn: sqlite3.Connection, table: str) -> str:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return str(row["n"] if row else "?")
    except sqlite3.Error:
        return "n/a"


def _futures_agent_counts() -> list[str]:
    lines: list[str] = []
    cfg = resolve_agent_db_config()
    lines.append(f"backend: {cfg.backend}")
    if cfg.database_name:
        lines.append(f"database: {cfg.database_name}")
    try:
        from bot.research.futures_agent.db import agent_connection

        with agent_connection() as conn:
            for table in ("futures_agent_inputs", "futures_agent_signals", "futures_agent_targets"):
                try:
                    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                    lines.append(f"  {table}: {row['n'] if row else '?'}")
                except Exception:
                    lines.append(f"  {table}: n/a")
    except Exception as exc:
        lines.append(f"  agent DB query failed: {type(exc).__name__}")
    return lines


def _shadow_candidate_summary(conn: sqlite3.Connection) -> list[str]:
    lines: list[str] = []
    try:
        rows = conn.execute(
            """
            SELECT id, qualified, enabled_shadow, created_at
            FROM ss_shadow_candidates
            ORDER BY id DESC LIMIT 10
            """
        ).fetchall()
        if not rows:
            lines.append("  no shadow candidates")
            return lines
        enabled = sum(1 for r in rows if r["enabled_shadow"])
        lines.append(f"  recent candidates: {len(rows)} (enabled_shadow in recent: {enabled})")
        for row in rows[:5]:
            lines.append(
                f"    id={row['id']} qualified={row['qualified']} "
                f"enabled_shadow={row['enabled_shadow']}"
            )
    except sqlite3.Error:
        lines.append("  ss_shadow_candidates: table missing or unreadable")
    return lines


def _diagnostics_gates() -> list[str]:
    lines = [
        "  shadow-enable: gated on split-diagnostics (use --force to override)",
        "  export-shadow: gated on split-diagnostics",
        "  live trading: NO-GO until TEST-era dense data + funnel validation",
        "  V4 collector: decoupled thread + MTF throttled (do not revert)",
    ]
    if ENABLE_V4_SHADOW:
        lines.append("  ENABLE_V4_SHADOW: true")
    else:
        lines.append("  ENABLE_V4_SHADOW: false")
    return lines


def generate_snapshot_text() -> str:
    bootstrap_config()
    git = read_git_status()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "POLYMARKET BTC5M BOT — OPS SNAPSHOT",
        f"generated: {now}",
        "",
        "GIT",
        f"  branch: {git.branch}",
        f"  commit: {git.commit_sha}",
        f"  dirty: {'yes' if git.dirty else 'no'}",
    ]
    if git.dirty_files:
        lines.append("  dirty files:")
        for path in git.dirty_files[:30]:
            lines.append(f"    {path}")
        if len(git.dirty_files) > 30:
            lines.append(f"    ... and {len(git.dirty_files) - 30} more")

    py = project_python()
    lines.extend(
        [
            "",
            "PYTHON",
            f"  executable: {py}",
            f"  version: {sys.version.split()[0]}",
            "",
            "PROCESSES",
        ]
    )
    main_procs = find_main_bot_processes()
    lines.append(f"  bot.main running: {'yes' if main_procs else 'no'} ({len(main_procs)} instance(s))")
    for proc in main_procs:
        lines.append(f"    PID {proc.pid}")
    tg_procs = find_telegram_poll_processes()
    lines.append(
        f"  telegram-poll running: {'yes' if tg_procs else 'no'} ({len(tg_procs)} instance(s))"
    )
    for proc in tg_procs:
        lines.append(f"    PID {proc.pid}")
    lines.append(f"  telegram lock: {assess_telegram_lock()}")

    lines.extend(["", "DATABASES", f"  SQLite path: {DATABASE_PATH}"])
    agent_cfg = resolve_agent_db_config()
    lines.append(f"  Futures agent backend: {agent_cfg.backend}")
    if agent_cfg.database_name:
        lines.append(f"  Futures agent DB name: {agent_cfg.database_name}")

    lines.extend(["", "SQLITE ROW COUNTS"])
    try:
        with connect() as conn:
            for table in RESEARCH_TABLES:
                if table.startswith("futures_agent"):
                    continue
                lines.append(f"  {table}: {_table_count(conn, table)}")
            collector = analyze_collector_health(conn, completed_limit=10)
            lines.extend(["", "V4 DENSITY (last completed markets)"])
            if collector.last_observation_ts:
                lines.append(f"  last observation ts: {collector.last_observation_ts}")
                lines.append(f"  age sec: {collector.last_observation_age_sec}")
            for stats in collector.completed_markets:
                lines.append(
                    f"  {stats.market_slug}: obs={stats.obs_count} "
                    f"gap={stats.median_gap_sec:.1f}s span={stats.collector_span_sec}s"
                )
            lines.extend(["", "SHADOW CANDIDATES"])
            lines.extend(_shadow_candidate_summary(conn))
    except sqlite3.Error as exc:
        lines.append(f"  SQLite error: {type(exc).__name__}")

    lines.extend(["", "FUTURES AGENT"])
    lines.extend(_futures_agent_counts())

    lines.extend(["", "DIAGNOSTICS GATES"])
    lines.extend(_diagnostics_gates())

    health = run_healthcheck()
    lines.extend(["", "HEALTHCHECK SUMMARY", f"  status: {health.status}"])
    for issue in health.issues[:20]:
        lines.append(f"  - {issue}")

    return redact_text("\n".join(lines))


def write_snapshot() -> Path:
    reports_dir = BASE_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"ops_snapshot_{stamp}.txt"
    path.write_text(generate_snapshot_text(), encoding="utf-8")
    return path


def main() -> int:
    path = write_snapshot()
    print(f"Snapshot written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
