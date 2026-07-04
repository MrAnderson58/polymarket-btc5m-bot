"""Bidirectional Shadow V1.1 health check CLI.

Usage: python -m bot.bidirectional_check
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys

from bot.database import connect, init_db
from bot.strategy.bidirectional_momentum import EntryConfig, REGIME_EXIT_PROFILES


def _check_process() -> str:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "python.*bot.main"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.stdout.strip() else "NOT RUNNING"
    except Exception:
        return "UNKNOWN"


def run_check() -> int:
    init_db()
    warnings = []

    with connect() as conn:
        from bot.strategy.bidirectional_shadow import ensure_tables
        ensure_tables(conn)

        # Observations
        obs_total = conn.execute(
            "SELECT COUNT(*) FROM bidirectional_shadow_observations"
        ).fetchone()[0]

        decisions = conn.execute("""
            SELECT decision, COUNT(*) as n
            FROM bidirectional_shadow_observations
            GROUP BY decision
        """).fetchall()
        dec_map = {r["decision"]: r["n"] for r in decisions}

        # Trades
        trades = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_n,
                   SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed_n,
                   SUM(CASE WHEN side='YES' AND status='closed' THEN 1 ELSE 0 END) as yes_closed,
                   SUM(CASE WHEN side='NO' AND status='closed' THEN 1 ELSE 0 END) as no_closed,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE 0 END) as gp,
                   SUM(CASE WHEN pnl_pct <= 0 THEN ABS(pnl_pct) ELSE 0 END) as gl
            FROM bidirectional_shadow_trades
        """).fetchone()

        open_n = trades["open_n"] or 0
        closed_n = trades["closed_n"] or 0
        gp = trades["gp"] or 0
        gl = trades["gl"] or 0
        total_pf = gp / gl if gl else 0.0

        yes_pf = _side_pf(conn, "YES")
        no_pf = _side_pf(conn, "NO")

        # Last timestamps
        last_obs = conn.execute(
            "SELECT MAX(timestamp) FROM bidirectional_shadow_observations"
        ).fetchone()[0]
        last_entry = conn.execute(
            "SELECT MAX(entry_ts) FROM bidirectional_shadow_trades"
        ).fetchone()[0]
        last_exit = conn.execute(
            "SELECT MAX(closed_at) FROM bidirectional_shadow_trades WHERE status='closed'"
        ).fetchone()[0]

        # Duplicate check
        dupes = conn.execute("""
            SELECT market_slug, COUNT(*) as n
            FROM bidirectional_shadow_trades
            WHERE status='open'
            GROUP BY market_slug HAVING n > 1
        """).fetchall()

        # Runtime errors (no error log table, just check for duplicates)
        if dupes:
            warnings.append(f"DUPLICATE OPEN TRADES: {len(dupes)} markets")

    # Process check
    process_status = _check_process()

    # Health determination
    health = "OK"
    if obs_total == 0 and process_status != "NOT RUNNING":
        health = "WARNING"
        warnings.append("Bot running but no observations recorded")
    if dupes:
        health = "FAIL"

    # Output
    print("=" * 50)
    print("BIDIRECTIONAL SHADOW V1.1")
    print("=" * 50)
    print()
    print(f"Status: {'RUNNING' if obs_total > 0 else 'NOT RUNNING'}")
    print(f"Bot process: {process_status}")
    print()
    print(f"Observations: {obs_total}")
    print(f"Decisions YES: {dec_map.get('YES', 0)}")
    print(f"Decisions NO: {dec_map.get('NO', 0)}")
    print(f"Decisions SKIP: {dec_map.get('SKIP', 0)}")
    print()
    print(f"Open virtual trades: {open_n}")
    print(f"Closed virtual trades: {closed_n}")
    print()
    print(f"YES PF: {yes_pf:.3f}")
    print(f"NO PF: {no_pf:.3f}")
    print(f"Total PF: {total_pf:.3f}")
    print()
    print(f"Last observation: {last_obs or 'none'}")
    print(f"Last virtual entry: {last_entry or 'none'}")
    print(f"Last virtual exit: {last_exit or 'none'}")
    print()
    print(f"Duplicate markets: {len(dupes) if dupes else 0}")
    print(f"Runtime errors: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    print()
    print("Config:")
    cfg = EntryConfig()
    print(f"  skip_regimes: {cfg.skip_regimes}")
    print(f"  NO avoid zone: {cfg.no_avoid_zone_lo}-{cfg.no_avoid_zone_hi}")
    print(f"  min_confidence: {cfg.min_confidence}")
    print(f"  min_move_30s: {cfg.min_move_30s}")
    print(f"  window: {cfg.min_seconds_from_start}-{cfg.max_seconds_from_start}s")
    print(f"  max_spread: {cfg.max_spread}")
    print(f"  YES max_ask: {cfg.yes_max_ask}")
    print(f"  NO max_ask: {cfg.no_max_ask}")
    print(f"  min_consistency: {cfg.min_consistency}")
    print()
    print(f"HEALTH: {health}")
    print("=" * 50)

    return 0 if health == "OK" else 1


def _side_pf(conn: sqlite3.Connection, side: str) -> float:
    row = conn.execute("""
        SELECT SUM(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE 0 END) as gp,
               SUM(CASE WHEN pnl_pct <= 0 THEN ABS(pnl_pct) ELSE 0 END) as gl
        FROM bidirectional_shadow_trades
        WHERE side = ? AND status = 'closed'
    """, (side,)).fetchone()
    gp = row["gp"] or 0
    gl = row["gl"] or 0
    return gp / gl if gl else 0.0


if __name__ == "__main__":
    sys.exit(run_check())
