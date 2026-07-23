"""S60 research stress test — parallel analytics while live DB is busy."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)


def run_research_stress_test(
    *,
    workers: int = 100,
    live_ticks: int = 50,
) -> dict[str, Any]:
    """Fire N parallel research readers/writers + live paper ticks.

    Success criterion: zero 'database is locked' errors on either DB.
    """
    from bot.research.market_events.db import is_database_locked, market_events_connection
    from bot.research.market_events.signal_intelligence.decision_trace_s58 import (
        format_decision_report,
    )
    from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
        format_feature_lab_report,
        run_feature_lab,
    )
    from bot.research.market_events.signal_intelligence.market_regime_s57 import (
        format_regime_report,
    )
    from bot.research.market_events.signal_intelligence.research_repository_s60 import (
        apply_research_migrations,
        research_connection,
    )
    from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
        format_paper_performance_s42,
        tick_open_paper_trades_s42,
    )
    from bot.research.market_events.signal_intelligence.trade_postmortem_s56 import (
        format_postmortem_report,
    )

    with research_connection() as conn:
        apply_research_migrations(conn)
        try:
            conn.commit()
        except Exception:
            pass

    # Ensure live operational schema exists once (DDL only before stress, never mid-run).
    from bot.research.market_events.event_schema import apply_migrations

    with market_events_connection() as conn:
        apply_migrations(conn)
        try:
            conn.commit()
        except Exception:
            pass

    lock_errors: list[str] = []
    other_errors: list[str] = []
    ok_counts = {"report": 0, "postmortem": 0, "feature_lab": 0, "decision": 0, "live": 0}
    lock = threading.Lock()

    def _note_exc(exc: BaseException, label: str) -> None:
        with lock:
            if is_database_locked(exc):
                lock_errors.append(f"{label}: {exc}")
            else:
                other_errors.append(f"{label}: {exc}")

    def research_job(kind: str, idx: int) -> str:
        try:
            with research_connection() as conn:
                if kind == "report":
                    format_regime_report(conn)
                    format_postmortem_report(conn)
                elif kind == "postmortem":
                    format_postmortem_report(conn)
                elif kind == "feature_lab":
                    if idx % 17 == 0:
                        run_feature_lab(conn, with_llm=False)
                    else:
                        format_feature_lab_report(conn)
                elif kind == "decision":
                    format_decision_report(conn)
                try:
                    conn.commit()
                except Exception:
                    pass
            with lock:
                ok_counts[kind] = ok_counts.get(kind, 0) + 1
            return kind
        except Exception as exc:
            _note_exc(exc, f"research:{kind}:{idx}")
            return f"err:{kind}"

    def live_job(idx: int) -> str:
        try:
            with market_events_connection() as conn:
                tick_open_paper_trades_s42(conn)
                format_paper_performance_s42(conn)
                try:
                    conn.commit()
                except Exception:
                    pass
            with lock:
                ok_counts["live"] += 1
            return "live"
        except Exception as exc:
            _note_exc(exc, f"live:{idx}")
            return "err:live"

    kinds = ["report", "postmortem", "feature_lab", "decision"]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(8, min(workers, 64))) as pool:
        futs = []
        for i in range(workers):
            futs.append(pool.submit(research_job, kinds[i % len(kinds)], i))
        for i in range(live_ticks):
            futs.append(pool.submit(live_job, i))
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as exc:
                _note_exc(exc, "future")

    elapsed = round(time.time() - t0, 3)
    return {
        "ok": len(lock_errors) == 0,
        "workers": workers,
        "live_ticks": live_ticks,
        "elapsed_sec": elapsed,
        "ok_counts": ok_counts,
        "lock_errors": lock_errors[:20],
        "lock_error_count": len(lock_errors),
        "other_errors": other_errors[:20],
        "other_error_count": len(other_errors),
    }


def format_research_stress_report(result: dict[str, Any]) -> str:
    lines = [
        "S60 Research Stress Test",
        f"  ok={result.get('ok')}  workers={result.get('workers')}  "
        f"live_ticks={result.get('live_ticks')}  elapsed={result.get('elapsed_sec')}s",
        f"  ok_counts={result.get('ok_counts')}",
        f"  lock_errors={result.get('lock_error_count')}  "
        f"other_errors={result.get('other_error_count')}",
    ]
    for err in result.get("lock_errors") or []:
        lines.append(f"  LOCK: {err}")
    for err in (result.get("other_errors") or [])[:5]:
        lines.append(f"  ERR: {err}")
    if result.get("ok"):
        lines.append("  Result: 0 database locked")
    return "\n".join(lines)


__all__ = [
    "format_research_stress_report",
    "run_research_stress_test",
]
