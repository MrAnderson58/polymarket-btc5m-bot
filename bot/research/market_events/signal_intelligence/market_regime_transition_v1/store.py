"""Persist transition library to SQLite (batched)."""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

from bot.research.market_events.research_db_session import research_write_lock
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.schema import (
    EDGES_TABLE,
    SEQUENCES_TABLE,
    TRANSITIONS_TABLE,
    ensure_regime_transition_schema,
)


def persist_library(
    conn: Any,
    *,
    transitions: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    sequences: Sequence[dict[str, Any]],
) -> dict[str, int]:
    ensure_regime_transition_schema(conn)
    now = int(time.time())
    t_rows: list[tuple] = []
    e_rows: list[tuple] = []
    s_rows: list[tuple] = []

    with research_write_lock():
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        try:
            conn.execute(f"DELETE FROM {TRANSITIONS_TABLE}")
            conn.execute(f"DELETE FROM {EDGES_TABLE}")
            conn.execute(f"DELETE FROM {SEQUENCES_TABLE}")

            t_sql = f"""
            INSERT INTO {TRANSITIONS_TABLE} (
                transition_key, lookback, from_state, to_state, pattern,
                n, wr, pf, ev, sharpe, max_dd, ci_lo, ci_hi, perm_p,
                oos_wr, oos_ev, temporal_stability, ready, score, kind, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
            for r in transitions:
                t_rows.append((
                    r.get("transition_key"),
                    int(r.get("lookback") or 0),
                    r.get("from_state"),
                    r.get("to_state"),
                    r.get("pattern"),
                    int(r.get("n") or r.get("trades") or 0),
                    r.get("wr"),
                    r.get("pf"),
                    r.get("ev"),
                    r.get("sharpe"),
                    r.get("max_dd"),
                    r.get("ci_lo"),
                    r.get("ci_hi"),
                    r.get("perm_p"),
                    r.get("oos_wr"),
                    r.get("oos_ev"),
                    r.get("temporal_stability"),
                    1 if r.get("ready") else 0,
                    r.get("score"),
                    r.get("kind"),
                    json.dumps({k: r.get(k) for k in ("ci_lo", "ci_hi", "perm_p")}, default=str),
                    now,
                ))
            if t_rows:
                conn.executemany(t_sql, t_rows)

            e_sql = f"""
            INSERT INTO {EDGES_TABLE} (
                from_state, to_state, count, prob, expected_duration,
                expected_ev, expected_wr, expected_pf
            ) VALUES (?,?,?,?,?,?,?,?)
            """
            for e in edges:
                e_rows.append((
                    e.get("from_state"), e.get("to_state"), int(e.get("count") or 0),
                    e.get("prob"), e.get("expected_duration"),
                    e.get("expected_ev"), e.get("expected_wr"), e.get("expected_pf"),
                ))
            if e_rows:
                conn.executemany(e_sql, e_rows)

            s_sql = f"""
            INSERT INTO {SEQUENCES_TABLE} (
                sequence_key, chain, depth, n, wr, pf, ev, sharpe, ready, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """
            for s in sequences:
                s_rows.append((
                    s.get("sequence_key"), s.get("chain"), int(s.get("depth") or 0),
                    int(s.get("n") or s.get("trades") or 0),
                    s.get("wr"), s.get("pf"), s.get("ev"), s.get("sharpe"),
                    1 if s.get("ready") else 0,
                    json.dumps({"score": s.get("score")}, default=str),
                    now,
                ))
            if s_rows:
                conn.executemany(s_sql, s_rows)

            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    return {
        "transitions": len(t_rows),
        "edges": len(e_rows),
        "sequences": len(s_rows),
    }


__all__ = ["persist_library"]
