"""Persist alpha_validations + alpha_validation_history."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from bot.research.market_events.signal_intelligence.alpha_validation_v2.schema import (
    ensure_alpha_validation_schema,
)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def new_run_id() -> str:
    return f"av2-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def start_history_run(
    conn: Any,
    *,
    run_id: str,
    n_candidates: int,
    n_rows: int,
) -> int:
    ensure_alpha_validation_schema(conn)
    cur = conn.execute(
        """
        INSERT INTO alpha_validation_history
            (run_id, started_at, n_candidates, n_passed, n_rejected, n_insufficient, n_rows)
        VALUES (?, ?, ?, 0, 0, 0, ?)
        """,
        (run_id, int(time.time()), int(n_candidates), int(n_rows)),
    )
    return int(cur.lastrowid)


def finish_history_run(
    conn: Any,
    *,
    run_id: str,
    n_passed: int,
    n_rejected: int,
    n_insufficient: int,
    summary: dict[str, Any],
    report_path: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE alpha_validation_history
           SET finished_at = ?,
               n_passed = ?,
               n_rejected = ?,
               n_insufficient = ?,
               summary_json = ?,
               report_path = ?
         WHERE run_id = ?
        """,
        (
            int(time.time()),
            int(n_passed),
            int(n_rejected),
            int(n_insufficient),
            _dumps(summary),
            report_path,
            run_id,
        ),
    )


def upsert_validation(conn: Any, *, run_id: str, result: dict[str, Any]) -> None:
    ensure_alpha_validation_schema(conn)
    ci = result.get("ci_ev") or (None, None)
    ci_lo = ci[0] if isinstance(ci, (list, tuple)) and len(ci) >= 1 else None
    ci_hi = ci[1] if isinstance(ci, (list, tuple)) and len(ci) >= 2 else None
    pf = result.get("pf")
    if pf == "inf" or (isinstance(result.get("metrics"), dict) and result["metrics"].get("pf_inf")):
        pf_f: float | None = float("inf")
    elif pf is None:
        pf_f = None
    else:
        pf_f = float(pf)
    conn.execute(
        """
        INSERT INTO alpha_validations (
            run_id, rule_id, rule_label, features_json, status, reject_reason,
            n_total, n_matched, expectancy, pf, winrate, sharpe,
            ci_lo, ci_hi, p_value,
            walk_forward_json, rolling_json, oos_json, monte_carlo_json,
            stability_json, metrics_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, rule_id) DO UPDATE SET
            status=excluded.status,
            reject_reason=excluded.reject_reason,
            n_total=excluded.n_total,
            n_matched=excluded.n_matched,
            expectancy=excluded.expectancy,
            pf=excluded.pf,
            winrate=excluded.winrate,
            sharpe=excluded.sharpe,
            ci_lo=excluded.ci_lo,
            ci_hi=excluded.ci_hi,
            p_value=excluded.p_value,
            walk_forward_json=excluded.walk_forward_json,
            rolling_json=excluded.rolling_json,
            oos_json=excluded.oos_json,
            monte_carlo_json=excluded.monte_carlo_json,
            stability_json=excluded.stability_json,
            metrics_json=excluded.metrics_json,
            created_at=excluded.created_at
        """,
        (
            run_id,
            result.get("rule_id"),
            result.get("rule_label"),
            _dumps(result.get("features") or []),
            result.get("status"),
            result.get("reject_reason"),
            result.get("n_total"),
            result.get("n_matched"),
            result.get("expectancy"),
            pf_f,
            result.get("winrate"),
            result.get("sharpe"),
            ci_lo,
            ci_hi,
            result.get("p_value"),
            _dumps(result.get("walk_forward") or {}),
            _dumps(result.get("rolling") or {}),
            _dumps(result.get("oos") or {}),
            _dumps(result.get("monte_carlo") or {}),
            _dumps(result.get("stability") or {}),
            _dumps(result.get("metrics") or {}),
            int(time.time()),
        ),
    )


def latest_run_id(conn: Any) -> str | None:
    ensure_alpha_validation_schema(conn)
    row = conn.execute(
        """
        SELECT run_id FROM alpha_validation_history
        ORDER BY started_at DESC, id DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return str(row[0] if not isinstance(row, dict) else row["run_id"])


def load_validations(conn: Any, *, run_id: str | None = None) -> list[dict[str, Any]]:
    ensure_alpha_validation_schema(conn)
    rid = run_id or latest_run_id(conn)
    if not rid:
        return []
    rows = conn.execute(
        """
        SELECT * FROM alpha_validations
         WHERE run_id = ?
         ORDER BY
           CASE status WHEN 'PASSED' THEN 0 WHEN 'REJECTED' THEN 1 ELSE 2 END,
           COALESCE(expectancy, -1e99) DESC,
           id ASC
        """,
        (rid,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else dict(r)
        # sqlite Row
        if hasattr(r, "keys"):
            d = {k: r[k] for k in r.keys()}
        out.append(d)
    return out


def load_history(conn: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    ensure_alpha_validation_schema(conn)
    rows = conn.execute(
        """
        SELECT * FROM alpha_validation_history
        ORDER BY started_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out = []
    for r in rows:
        if hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append(dict(r))
    return out


__all__ = [
    "finish_history_run",
    "latest_run_id",
    "load_history",
    "load_validations",
    "new_run_id",
    "start_history_run",
    "upsert_validation",
]
