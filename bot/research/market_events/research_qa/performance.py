"""Light performance benches for Research QA."""

from __future__ import annotations

import os
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.research_qa.golden_dataset import generate_golden_trades, seed_golden_s55
from bot.research.market_events.research_qa.pipeline import run_full_research_pipeline

# Soft budgets for light CI (seconds). Full suite can use larger N offline.
LIGHT_SIZES = (100, 1000)
FULL_SIZES = (100, 1000, 5000, 10000)

# Generous ceilings — catch pathological regressions, not micro-opts.
TIME_BUDGET_S = {
    100: 30.0,
    1000: 90.0,
    5000: 300.0,
    10000: 600.0,
}


def _rss_mb() -> float | None:
    try:
        import resource

        # macOS: ru_maxrss is bytes; Linux: kilobytes
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if rss > 10_000_000:  # likely bytes (darwin)
            return round(rss / (1024 * 1024), 2)
        return round(rss / 1024.0, 2)
    except Exception:
        return None


def run_performance_suite(
    *,
    light: bool = True,
    sizes: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """
    Seed synthetic corpora of increasing size and time the full research pipeline.
    Returns rows + pass/fail vs soft budgets.
    """
    chosen = sizes or (LIGHT_SIZES if light else FULL_SIZES)
    rows: list[dict[str, Any]] = []
    fails: list[str] = []

    for n in chosen:
        # Cap golden generator at 300 — for larger N, tile the golden corpus
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / f"perf_{n}.db"
            configure_unit_test_db_isolation(db_path)
            reports = Path(td) / "reports"
            t0 = time.perf_counter()
            tracemalloc.start()
            with market_events_connection() as conn:
                apply_migrations(conn)
                base = generate_golden_trades(200)
                # Tile + remint ids for larger synthetic sets
                trades: list[dict[str, Any]] = []
                i = 0
                while len(trades) < n:
                    for t in base:
                        row = dict(t)
                        row["s40_signal_id"] = i + 1
                        row["closed_at"] = int(t["closed_at"]) + i
                        row["created_at"] = int(t["created_at"]) + i
                        trades.append(row)
                        i += 1
                        if len(trades) >= n:
                            break
                seed_golden_s55(conn, trades)
                run_full_research_pipeline(conn, reports_root=reports, patterns_root=reports)
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            elapsed = time.perf_counter() - t0
            db_size = db_path.stat().st_size if db_path.exists() else 0
            row = {
                "n": n,
                "duration_s": round(elapsed, 3),
                "peak_tracemalloc_mb": round(peak / (1024 * 1024), 2),
                "rss_mb": _rss_mb(),
                "db_bytes": db_size,
                "db_mb": round(db_size / (1024 * 1024), 3),
            }
            rows.append(row)
            budget = TIME_BUDGET_S.get(n, 600.0)
            if elapsed > budget:
                fails.append(f"n={n} took {elapsed:.1f}s > budget {budget:.0f}s")

    return {"rows": rows, "fails": fails, "light": light}
