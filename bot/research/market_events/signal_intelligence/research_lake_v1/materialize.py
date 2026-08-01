"""Research-only materialization of CLOSED trades for Research Lake coverage.

Backfills missing CLOSED rows into market_events_paper_trades_s42 from S40 reviews
so build-research-lake can load the full closed book. Does not open live paper
positions, does not change Gate/Strategy/Execution, and does not touch equity.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_S42 = "market_events_paper_trades_s42"
_S40 = "market_events_signal_learning_s40_signals"
_REV = "market_events_signal_learning_s40_reviews"

EXIT_REASON = "research_s40_review"
BATCH = 2000


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _result_from_pnl(pnl: float) -> str:
    if pnl > 0.05:
        return "WIN"
    if pnl < -0.05:
        return "LOSS"
    return "BE"


def count_s42_closed(conn: Any) -> int:
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) FROM {_S42} WHERE status='CLOSED' AND pnl_pct IS NOT NULL"
            ).fetchone()[0]
        )
    except Exception:
        return 0


def count_s40_reviewed_missing(conn: Any) -> int:
    try:
        return int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {_S40} s
                JOIN {_REV} r
                  ON r.signal_type = s.signal_type AND r.signal_id = s.signal_id
                LEFT JOIN {_S42} p
                  ON p.s40_signal_type = s.signal_type AND p.s40_signal_id = s.signal_id
                WHERE p.id IS NULL
                  AND s.entry IS NOT NULL AND s.entry > 0
                  AND UPPER(s.direction) IN ('LONG', 'SHORT')
                  AND r.pnl_pct IS NOT NULL
                """
            ).fetchone()[0]
        )
    except Exception:
        return 0


def materialize_closed_from_s40_reviews(
    conn: Any,
    *,
    limit: int | None = None,
    batch_size: int = BATCH,
) -> dict[str, Any]:
    """Insert CLOSED S42 rows for reviewed S40 signals missing from paper trades."""
    t0 = time.time()
    before = count_s42_closed(conn)
    missing = count_s40_reviewed_missing(conn)
    sql = f"""
        SELECT s.signal_type, s.signal_id, s.symbol, s.direction,
               s.entry, s.stop, s.tp1, s.tp2, s.timestamp,
               s.snapshot_decision_confidence, s.snapshot_pattern_json, s.snapshot_news_impact,
               r.pnl_pct, r.rr_achieved, r.win_loss_be, r.reviewed_at
        FROM {_S40} s
        JOIN {_REV} r
          ON r.signal_type = s.signal_type AND r.signal_id = s.signal_id
        LEFT JOIN {_S42} p
          ON p.s40_signal_type = s.signal_type AND p.s40_signal_id = s.signal_id
        WHERE p.id IS NULL
          AND s.entry IS NOT NULL AND s.entry > 0
          AND UPPER(s.direction) IN ('LONG', 'SHORT')
          AND r.pnl_pct IS NOT NULL
        ORDER BY s.timestamp ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"

    try:
        rows = conn.execute(sql).fetchall()
    except Exception as exc:
        logger.warning("materialize s40 query failed: %s", exc)
        return {
            "ok": False,
            "error": str(exc),
            "inserted": 0,
            "before_closed": before,
            "after_closed": before,
            "missing_before": missing,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    inserted = 0
    now = int(time.time())
    params_batch: list[tuple[Any, ...]] = []

    def _flush() -> None:
        nonlocal params_batch, inserted
        if not params_batch:
            return
        conn.executemany(
            f"""
            INSERT OR IGNORE INTO {_S42} (
              s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
              mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason, exit_price,
              rr_achieved, status, decision_confidence, pattern_json, news_category,
              capital_usd, leverage, updated_at
            ) VALUES (
              ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            params_batch,
        )
        inserted += len(params_batch)
        params_batch = []
        try:
            conn.commit()
        except Exception:
            pass

    for r in rows:
        d = {k: r[k] for k in r.keys()} if hasattr(r, "keys") else {}
        if not d:
            continue
        entry = float(d["entry"])
        pnl_pct = float(d["pnl_pct"])
        is_long = str(d["direction"]).upper() == "LONG"
        # Reconstruct exit from pnl_pct
        exit_price = entry * (1.0 + pnl_pct / 100.0) if is_long else entry * (1.0 - pnl_pct / 100.0)
        created = int(d.get("timestamp") or now)
        closed = int(d.get("reviewed_at") or created)
        if closed < created:
            closed = created
        holding = max(0, closed - created)
        result = str(d.get("win_loss_be") or _result_from_pnl(pnl_pct)).upper()
        if result not in ("WIN", "LOSS", "BE"):
            result = _result_from_pnl(pnl_pct)
        mfe = max(0.0, pnl_pct)
        mae = min(0.0, pnl_pct)
        params_batch.append(
            (
                str(d["signal_type"]),
                int(d["signal_id"]),
                str(d.get("symbol") or ""),
                str(d["direction"]).upper(),
                entry,
                _safe_float(d.get("stop")),
                _safe_float(d.get("tp1")),
                _safe_float(d.get("tp2")),
                created,
                closed,
                holding,
                round(mfe, 4),
                round(mae, 4),
                round(pnl_pct, 4),
                round(pnl_pct, 4),  # pnl_usd proxy (research backfill; not live equity)
                result,
                EXIT_REASON,
                round(exit_price, 8),
                _safe_float(d.get("rr_achieved")),
                "CLOSED",
                _safe_float(d.get("snapshot_decision_confidence")),
                d.get("snapshot_pattern_json"),
                d.get("snapshot_news_impact"),
                100.0,
                1.0,
                now,
            )
        )
        if len(params_batch) >= batch_size:
            _flush()
    _flush()

    after = count_s42_closed(conn)
    return {
        "ok": True,
        "inserted": inserted,
        "before_closed": before,
        "after_closed": after,
        "missing_before": missing,
        "missing_after": count_s40_reviewed_missing(conn),
        "elapsed_sec": round(time.time() - t0, 3),
        "research_only": True,
        "exit_reason": EXIT_REASON,
    }


__all__ = [
    "count_s40_reviewed_missing",
    "count_s42_closed",
    "materialize_closed_from_s40_reviews",
]
