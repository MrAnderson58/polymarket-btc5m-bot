"""Persist Elite Market Profile tables (batched)."""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

from bot.research.market_events.research_db_session import research_write_lock
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.schema import (
    COMBOS_TABLE,
    COMPARE_TABLE,
    PROFILE_TABLE,
    ensure_elite_market_profile_schema,
)


def persist_profile(
    conn: Any,
    *,
    dimensions: Sequence[dict[str, Any]],
    combos: Sequence[dict[str, Any]],
    compare: Sequence[dict[str, Any]],
) -> dict[str, int]:
    ensure_elite_market_profile_schema(conn)
    now = int(time.time())
    with research_write_lock():
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        try:
            conn.execute(f"DELETE FROM {PROFILE_TABLE}")
            conn.execute(f"DELETE FROM {COMBOS_TABLE}")
            conn.execute(f"DELETE FROM {COMPARE_TABLE}")

            d_sql = f"""
            INSERT INTO {PROFILE_TABLE} (
                dimension, key, n, pct, wr, pf, ev, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """
            d_rows = [(
                d.get("dimension"),
                d.get("key"),
                int(d.get("n") or 0),
                d.get("pct"),
                d.get("wr"),
                d.get("pf"),
                d.get("ev"),
                json.dumps(d.get("meta") or {}, ensure_ascii=False),
                now,
            ) for d in dimensions]
            if d_rows:
                conn.executemany(d_sql, d_rows)

            c_sql = f"""
            INSERT INTO {COMBOS_TABLE} (
                combo_key, pattern, tags_json, n, wr, pf, ev, rank, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """
            c_rows = [(
                c.get("combo_key"),
                c.get("pattern"),
                json.dumps(c.get("tags") or [], ensure_ascii=False),
                int(c.get("n") or 0),
                c.get("wr"),
                c.get("pf"),
                c.get("ev"),
                c.get("rank"),
                json.dumps({}, ensure_ascii=False),
                now,
            ) for c in combos]
            if c_rows:
                conn.executemany(c_sql, c_rows)

            v_sql = f"""
            INSERT INTO {COMPARE_TABLE} (
                feature, elite_pct, ignore_pct, delta_pct, elite_n, ignore_n, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """
            v_rows = [(
                v.get("feature"),
                v.get("elite_pct"),
                v.get("ignore_pct"),
                v.get("delta_pct"),
                int(v.get("elite_n") or 0),
                int(v.get("ignore_n") or 0),
                json.dumps({}, ensure_ascii=False),
                now,
            ) for v in compare]
            if v_rows:
                conn.executemany(v_sql, v_rows)

            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    return {"dimensions": len(d_rows), "combos": len(c_rows), "compare": len(v_rows)}


__all__ = ["persist_profile"]
