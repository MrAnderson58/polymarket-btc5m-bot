"""Persist error learning tables (batched, single writer)."""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

from bot.research.market_events.research_db_session import research_write_lock
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.schema import (
    ERRORS_TABLE,
    PATTERNS_TABLE,
    ensure_decision_error_schema,
)


def persist_errors(
    conn: Any,
    *,
    records: Sequence[dict[str, Any]],
    patterns: Sequence[dict[str, Any]],
    book: str = "paper_decision",
) -> dict[str, int]:
    ensure_decision_error_schema(conn)
    now = int(time.time())
    with research_write_lock():
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        try:
            conn.execute(f"DELETE FROM {ERRORS_TABLE}")
            conn.execute(f"DELETE FROM {PATTERNS_TABLE}")

            e_sql = f"""
            INSERT INTO {ERRORS_TABLE} (
                trade_id, symbol, opened_at, confusion, error_class, book,
                pnl, direction, decision, confidence, primary_module,
                reasons_json, modules_json, recovered_ev_if_ignored, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
            e_rows = []
            for r in records:
                if r.get("confusion") not in ("FN", "FP", "TP", "TN"):
                    continue
                # store only mistakes + sample of corrects? store all for learning
                e_rows.append((
                    int(r.get("trade_id") or 0),
                    r.get("symbol"),
                    r.get("opened_at"),
                    r.get("confusion"),
                    r.get("error_class"),
                    book,
                    r.get("pnl"),
                    r.get("direction"),
                    r.get("decision"),
                    r.get("confidence"),
                    r.get("primary_module"),
                    json.dumps(r.get("reasons") or [], ensure_ascii=False),
                    json.dumps(r.get("modules") or {}, ensure_ascii=False),
                    r.get("recovered_ev_if_ignored"),
                    now,
                ))
            if e_rows:
                conn.executemany(e_sql, e_rows)

            p_sql = f"""
            INSERT INTO {PATTERNS_TABLE} (
                pattern_key, kind, pattern, primary_module, n,
                total_pnl, mean_pnl, wr, recovered_ev, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """
            p_rows = [(
                p.get("pattern_key"),
                p.get("kind"),
                p.get("pattern"),
                p.get("primary_module"),
                int(p.get("n") or 0),
                p.get("total_pnl"),
                p.get("mean_pnl"),
                p.get("wr"),
                p.get("recovered_ev"),
                json.dumps({
                    "error_class": p.get("error_class"),
                    "sample_trade_ids": p.get("sample_trade_ids"),
                    "sample_reasons": p.get("sample_reasons"),
                }, ensure_ascii=False),
                now,
            ) for p in patterns]
            if p_rows:
                conn.executemany(p_sql, p_rows)

            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    return {"errors": len(e_rows), "patterns": len(p_rows)}


__all__ = ["persist_errors"]
