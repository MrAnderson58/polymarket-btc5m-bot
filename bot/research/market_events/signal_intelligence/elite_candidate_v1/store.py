"""Persist elite candidates + learning history (batched)."""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

from bot.research.market_events.research_db_session import research_write_lock
from bot.research.market_events.signal_intelligence.elite_candidate_v1.schema import (
    CANDIDATES_TABLE,
    HISTORY_TABLE,
    ensure_elite_candidate_schema,
)


def persist_candidates(
    conn: Any,
    *,
    candidates: Sequence[dict[str, Any]],
    history: Sequence[dict[str, Any]] | None = None,
    replace: bool = True,
) -> dict[str, int]:
    ensure_elite_candidate_schema(conn)
    now = int(time.time())
    with research_write_lock():
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        try:
            if replace:
                conn.execute(f"DELETE FROM {CANDIDATES_TABLE}")
            c_sql = f"""
            INSERT OR REPLACE INTO {CANDIDATES_TABLE} (
                trade_id, symbol, opened_at, direction, category,
                score, base_score, learned_score,
                why_json, why_not_json, supporting_modules_json, rejecting_modules_json,
                historical_wr, historical_ev, historical_pf, historical_similarity,
                current_regime, current_transition, current_fingerprint,
                decision_confidence, brain_confidence,
                expected_ev, expected_holding_time, expected_drawdown,
                components_json, result, pnl, updated_at, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
            c_rows = []
            for c in candidates:
                c_rows.append((
                    int(c.get("trade_id") or 0),
                    c.get("symbol"),
                    c.get("opened_at"),
                    c.get("direction"),
                    c.get("category"),
                    float(c.get("score") or 0),
                    float(c.get("base_score") or c.get("score") or 0),
                    c.get("learned_score"),
                    json.dumps(c.get("why") or [], ensure_ascii=False),
                    json.dumps(c.get("why_not") or [], ensure_ascii=False),
                    json.dumps(c.get("supporting_modules") or [], ensure_ascii=False),
                    json.dumps(c.get("rejecting_modules") or [], ensure_ascii=False),
                    c.get("historical_wr"),
                    c.get("historical_ev"),
                    c.get("historical_pf"),
                    c.get("historical_similarity"),
                    c.get("current_regime"),
                    c.get("current_transition"),
                    c.get("current_fingerprint"),
                    c.get("decision_confidence"),
                    c.get("brain_confidence"),
                    c.get("expected_ev"),
                    c.get("expected_holding_time"),
                    c.get("expected_drawdown"),
                    json.dumps(c.get("components") or {}, ensure_ascii=False),
                    c.get("result"),
                    c.get("pnl"),
                    now,
                    int(c.get("created_at") or now),
                ))
            if c_rows:
                conn.executemany(c_sql, c_rows)

            h_n = 0
            if history:
                h_sql = f"""
                INSERT INTO {HISTORY_TABLE} (
                    trade_id, event, old_score, new_score, delta, pnl, result, note, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """
                h_rows = [(
                    int(h.get("trade_id") or 0),
                    h.get("event") or "learn",
                    h.get("old_score"),
                    h.get("new_score"),
                    h.get("delta"),
                    h.get("pnl"),
                    h.get("result"),
                    h.get("note"),
                    int(h.get("created_at") or now),
                ) for h in history]
                if h_rows:
                    conn.executemany(h_sql, h_rows)
                    h_n = len(h_rows)

            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    return {"candidates": len(c_rows), "history": h_n}


def load_stored_candidates(
    conn: Any,
    *,
    categories: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    ensure_elite_candidate_schema(conn)
    sql = f"SELECT * FROM {CANDIDATES_TABLE}"
    params: list[Any] = []
    if categories:
        placeholders = ",".join("?" for _ in categories)
        sql += f" WHERE category IN ({placeholders})"
        params.extend(list(categories))
    sql += " ORDER BY score DESC, opened_at DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        try:
            d = {k: r[k] for k in r.keys()}  # type: ignore[attr-defined]
        except Exception:
            d = dict(r)
        for key in ("why_json", "why_not_json", "supporting_modules_json", "rejecting_modules_json", "components_json"):
            raw = d.get(key)
            if isinstance(raw, str):
                try:
                    d[key.replace("_json", "")] = json.loads(raw)
                except Exception:
                    d[key.replace("_json", "")] = []
        out.append(d)
    return out


__all__ = ["load_stored_candidates", "persist_candidates"]
