"""Diagnose and repair missing S55 joins for Research Lake."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    LAKE_TABLE,
    ensure_research_lake_schema,
)

_S42 = "market_events_paper_trades_s42"
_S55 = "market_events_trade_features_s55"

REASONS = (
    "NO_S55_ROW",
    "NO_FOREIGN_KEY",
    "TIMESTAMP_MISMATCH",
    "SYMBOL_MISMATCH",
    "JOIN_KEY_ERROR",
    "DUPLICATE",
    "UNKNOWN",
)


def diagnose_missing_s55_joins(conn: Any, *, limit: int | None = None) -> dict[str, Any]:
    """Classify every lake row with s55_id IS NULL."""
    ensure_research_lake_schema(conn)
    sql = f"""
        SELECT l.trade_id, l.symbol, l.opened_at, l.closed_at,
               p.s40_signal_type, p.s40_signal_id, p.symbol AS p_symbol,
               p.created_at, p.closed_at AS p_closed
        FROM {LAKE_TABLE} l
        LEFT JOIN {_S42} p ON p.id = l.trade_id
        WHERE l.s55_id IS NULL
        ORDER BY l.trade_id ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()

    # Preload S55 indexes
    by_tid: dict[int, list[dict[str, Any]]] = {}
    by_s40: dict[tuple[str, int], list[dict[str, Any]]] = {}
    by_sym_close: dict[tuple[str, int], list[dict[str, Any]]] = {}
    try:
        for r in conn.execute(
            f"SELECT id, paper_trade_id, s40_signal_type, s40_signal_id, symbol, closed_at FROM {_S55}"
        ).fetchall():
            d = {
                "id": r[0],
                "paper_trade_id": r[1],
                "s40_signal_type": r[2],
                "s40_signal_id": r[3],
                "symbol": r[4],
                "closed_at": r[5],
            }
            if d["paper_trade_id"] is not None:
                by_tid.setdefault(int(d["paper_trade_id"]), []).append(d)
            if d["s40_signal_type"] is not None and d["s40_signal_id"] is not None:
                by_s40.setdefault((str(d["s40_signal_type"]), int(d["s40_signal_id"])), []).append(d)
            if d.get("symbol") and d.get("closed_at") is not None:
                # bucket by symbol + minute for fuzzy timestamp match
                bucket = int(d["closed_at"]) // 60
                by_sym_close.setdefault((str(d["symbol"]).upper(), bucket), []).append(d)
    except Exception:
        pass

    reasons: Counter[str] = Counter()
    samples: dict[str, list[int]] = {k: [] for k in REASONS}

    for r in rows:
        if hasattr(r, "keys"):
            trade_id = int(r["trade_id"])
            sym = str(r["symbol"] or "")
            s40_t = r["s40_signal_type"]
            s40_i = r["s40_signal_id"]
            p_sym = r["p_symbol"]
            closed = r["closed_at"] or r["p_closed"]
        else:
            trade_id, sym = int(r[0]), str(r[1] or "")
            s40_t, s40_i, p_sym = r[4], r[5], r[6]
            closed = r[3] or r[8]

        reason = "UNKNOWN"
        hits_tid = by_tid.get(trade_id) or []
        hits_s40 = []
        if s40_t is not None and s40_i is not None:
            hits_s40 = by_s40.get((str(s40_t), int(s40_i))) or []

        if hits_tid:
            if len(hits_tid) > 1:
                reason = "DUPLICATE"
            else:
                # Row exists but lake s55_id null → join key / FK bookkeeping error
                reason = "JOIN_KEY_ERROR"
        elif hits_s40:
            h = hits_s40[0]
            h_sym = str(h.get("symbol") or "").upper()
            want = sym.upper() or str(p_sym or "").upper()
            if h_sym and want and h_sym != want:
                reason = "SYMBOL_MISMATCH"
            elif h.get("paper_trade_id") is None or int(h.get("paper_trade_id") or -1) != trade_id:
                reason = "NO_FOREIGN_KEY"
            else:
                reason = "JOIN_KEY_ERROR"
        else:
            fuzzy = False
            try:
                c_i = int(closed) if closed is not None else None
            except Exception:
                c_i = None
            if c_i is not None and sym:
                bucket = c_i // 60
                for b in (bucket - 5, bucket, bucket + 5):
                    for h in by_sym_close.get((sym.upper(), b), []):
                        if abs(int(h.get("closed_at") or 0) - c_i) <= 300:
                            fuzzy = True
                            break
                    if fuzzy:
                        break
            reason = "TIMESTAMP_MISMATCH" if fuzzy else "NO_S55_ROW"

        reasons[reason] += 1
        if len(samples[reason]) < 8:
            samples[reason].append(trade_id)

    n_missing = sum(reasons.values())
    n_lake = int(conn.execute(f"SELECT COUNT(*) FROM {LAKE_TABLE}").fetchone()[0])
    pct = round(100.0 * n_missing / n_lake, 4) if n_lake else 0.0
    return {
        "n_lake": n_lake,
        "n_missing": n_missing,
        "missing_pct": pct,
        "reasons": dict(reasons),
        "samples": samples,
    }


def _hour_weekday(ts: int | None) -> tuple[int | None, int | None]:
    if not ts:
        return None, None
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return dt.hour, dt.weekday()


def repair_s55_joins(conn: Any, *, limit: int | None = None) -> dict[str, Any]:
    """
    1) Relink orphan S55 rows via s40 keys → paper_trade_id
    2) Insert research S55 stubs for CLOSED trades still missing S55
    3) Patch lake.s55_id
    """
    t0 = time.time()
    ensure_research_lake_schema(conn)
    before = diagnose_missing_s55_joins(conn)

    relinked = 0
    # Relink: S55 has s40 keys matching S42 but paper_trade_id null/wrong
    try:
        rows = conn.execute(
            f"""
            SELECT f.id, p.id AS paper_id
            FROM {_S55} f
            JOIN {_S42} p
              ON p.s40_signal_type = f.s40_signal_type
             AND p.s40_signal_id = f.s40_signal_id
            WHERE f.paper_trade_id IS NULL OR f.paper_trade_id != p.id
            """
        ).fetchall()
        for fid, pid in rows:
            conn.execute(
                f"UPDATE {_S55} SET paper_trade_id = ? WHERE id = ?",
                (int(pid), int(fid)),
            )
            relinked += 1
        if relinked:
            conn.commit()
    except Exception:
        pass

    # Materialize missing S55 for closed trades
    inserted = 0
    # S42 may lack gate_decision on older DBs — select only stable columns.
    sql_missing = f"""
        SELECT p.id, p.s40_signal_type, p.s40_signal_id, p.symbol, p.direction,
               p.created_at, p.closed_at, p.pnl_pct, p.pnl_usd, p.result,
               p.mae_pct, p.mfe_pct, p.exit_reason, p.holding_seconds,
               p.decision_confidence
        FROM {_S42} p
        LEFT JOIN {_S55} f ON f.paper_trade_id = p.id
        WHERE p.status='CLOSED' AND p.pnl_pct IS NOT NULL AND f.id IS NULL
        ORDER BY p.id ASC
    """
    if limit:
        sql_missing += f" LIMIT {int(limit)}"
    try:
        missing = conn.execute(sql_missing).fetchall()
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "before": before,
            "relinked": relinked,
            "inserted": 0,
        }

    now = int(time.time())
    batch: list[tuple[Any, ...]] = []

    def _flush() -> None:
        nonlocal batch, inserted
        if not batch:
            return
        conn.executemany(
            f"""
            INSERT OR IGNORE INTO {_S55} (
              paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
              hour, weekday, atr, rsi, funding, oi_delta, fear_greed, trend,
              volume, features_json, result, pnl_pct, pnl_usd, mae_pct, mfe_pct,
              duration_sec, exit_reason, gate_decision, created_at, closed_at,
              market_regime, news_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            batch,
        )
        inserted += len(batch)
        batch = []
        try:
            conn.commit()
        except Exception:
            pass

    for r in missing:
        d = {k: r[k] for k in r.keys()} if hasattr(r, "keys") else {}
        if not d:
            continue
        # Always unique research keys — UNIQUE(s40_type, s40_id) must not collide
        # across backfilled CLOSED trades that share live s40 keys or lack them.
        tid = int(d["id"])
        s40_t = f"research_s55:{tid}"
        s40_i = tid
        hour, weekday = _hour_weekday(d.get("created_at") or d.get("closed_at"))
        feats = {
            "source": "research_s55_backfill",
            "confidence": d.get("decision_confidence"),
            "hour": hour,
            "weekday": weekday,
            "orig_s40_signal_type": d.get("s40_signal_type"),
            "orig_s40_signal_id": d.get("s40_signal_id"),
        }
        batch.append(
            (
                tid,
                s40_t,
                s40_i,
                str(d.get("symbol") or ""),
                str(d.get("direction") or "").upper(),
                hour,
                weekday,
                None,  # atr
                None,  # rsi
                None,  # funding
                None,  # oi
                None,  # fear
                None,  # trend
                None,  # volume
                json.dumps(feats, default=str),
                d.get("result"),
                d.get("pnl_pct"),
                d.get("pnl_usd"),
                d.get("mae_pct"),
                d.get("mfe_pct"),
                d.get("holding_seconds"),
                d.get("exit_reason"),
                None,  # gate_decision (not on older S42)
                int(d.get("created_at") or now),
                d.get("closed_at"),
                None,
                None,
            )
        )
        if len(batch) >= 1000:
            _flush()
    _flush()

    # Patch lake s55_id from paper_trade_id
    patched = 0
    try:
        cur = conn.execute(
            f"""
            UPDATE {LAKE_TABLE}
            SET s55_id = (
              SELECT f.id FROM {_S55} f
              WHERE f.paper_trade_id = {LAKE_TABLE}.trade_id
              LIMIT 1
            )
            WHERE s55_id IS NULL
              AND EXISTS (
                SELECT 1 FROM {_S55} f WHERE f.paper_trade_id = {LAKE_TABLE}.trade_id
              )
            """
        )
        patched = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        conn.commit()
    except Exception:
        # SQLite may not report rowcount; recount
        pass

    # Recount patched if rowcount unreliable
    try:
        still = int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM {LAKE_TABLE} l
                WHERE l.s55_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM {_S55} f WHERE f.paper_trade_id = l.trade_id
                  )
                """
            ).fetchone()[0]
        )
        if still == 0 and patched == 0:
            # estimate from before/after later
            pass
    except Exception:
        pass

    after = diagnose_missing_s55_joins(conn)
    return {
        "ok": after["missing_pct"] < 1.0,
        "before": before,
        "after": after,
        "relinked": relinked,
        "inserted": inserted,
        "patched_lake": patched,
        "elapsed_sec": round(time.time() - t0, 3),
    }


__all__ = [
    "REASONS",
    "diagnose_missing_s55_joins",
    "repair_s55_joins",
]
