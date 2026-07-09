"""Polymarket paper resume audit for Phase E.1 — read-only."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def render_polymarket_paper_audit() -> str:
    from bot.config import (
        DATABASE_PATH,
        ENABLE_LATE_WINDOW,
        ENABLE_V2,
        ENABLE_V3,
        ENABLE_V4_SHADOW,
        TRADING_MODE,
    )

    lines = [
        "POLYMARKET PAPER AUDIT (read-only)",
        f"database: {DATABASE_PATH}",
        f"TRADING_MODE: {TRADING_MODE}",
        "",
        "=== 1. IMPLEMENTED STRATEGIES ===",
        "  Late-window virtual trades (ENABLE_LATE_WINDOW): paper via virtual_trades",
        "  Early Reversion v1: early_reversion_trades",
        "  Early Reversion v2 (production default): early_reversion_v2_trades",
        "  Early Reversion v2.5: early_reversion_v25_trades",
        "  Early Reversion v3: early_reversion_v3_trades",
        "  V4 shadow: v4_shadow_trades (observe-only)",
        "  Bidirectional shadow v1.1/v1.2: observe-only",
        "  YES_C / NO_C shadow: observe-only",
        "",
        "=== 2. PAPER vs LIVE ===",
        f"  ENABLE_V2: {ENABLE_V2}",
        f"  ENABLE_V3: {ENABLE_V3}",
        f"  ENABLE_V4_SHADOW: {ENABLE_V4_SHADOW}",
        f"  ENABLE_LATE_WINDOW: {ENABLE_LATE_WINDOW}",
        "  Live orders only when TRADING_MODE=live via bot/execution.py",
        "  Phase E.1 does NOT start bot.main",
        "",
        "=== 3. ACTIVELY WRITTEN TABLES (when bot.main runs) ===",
        "  market_checks, virtual_trades, early_reversion_*_trades,",
        "  v4_shadow_observations, multi_timeframe_snapshots, bidirectional_shadow_*",
        "",
        "=== 4. CONCURRENT WITH PHASE E ===",
        "  YES — Phase E uses isolated data/market_events.db",
        "  bot.main uses data/trades.db — separate SQLite file, no writer conflict",
        "  futures_agent uses separate agent DB — read-only links from E.1",
        "",
        "=== 5. SAFE PAPER RESUME (Polymarket) ===",
        "  Do NOT use bot.main with TRADING_MODE=live",
        "  Safe paper command:",
        "    TRADING_MODE=paper ENABLE_V2=true ENABLE_V4_SHADOW=true \\",
        "    python -m bot.main",
        "  Or use deploy wrapper:",
        "    deploy/macos/run-bot-main.sh  (verify TRADING_MODE=paper in .env)",
        "",
        "=== 6. BTC SHOCK → POLYMARKET LINK ===",
        "  Phase E links market_checks rows within 30m of shock via market_event_context",
        "  context_type=POLYMARKET_STATE, source_record_id=market_checks.id",
        "",
        "=== 7. RECOMMENDATION ===",
        "  Keep Polymarket bot stopped during initial E.1 shock collection OR run concurrently",
        "  (separate DB). Do not enable live orders. Use shock-paper-run for perp shock paper.",
    ]

    if Path(DATABASE_PATH).exists():
        try:
            conn = sqlite3.connect(str(DATABASE_PATH))
            conn.row_factory = sqlite3.Row
            for table, label in (
                ("virtual_trades", "virtual_trades"),
                ("early_reversion_v2_trades", "er_v2_trades"),
                ("market_checks", "market_checks"),
                ("v4_shadow_observations", "v4_observations"),
            ):
                try:
                    n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                    lines.append(f"  {label}_count: {n:,}")
                except sqlite3.Error:
                    lines.append(f"  {label}_count: (table missing)")
            conn.close()
        except Exception as exc:
            lines.append(f"  db_read_error: {exc}")

    return "\n".join(lines)
