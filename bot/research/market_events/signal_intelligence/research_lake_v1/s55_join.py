"""Diagnose / audit / repair S55 joins for Research Lake.

Audit-only taxonomy for missing (or stub-only) S55 coverage.
"""

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

# Audit reason codes (S55 Join Audit V1).
REASONS = (
    "NO_S55_RECORD",
    "EXPECTED_NO_S55",
    "MATERIALIZED_S40",
    "LEGACY_TRADE",
    "JOIN_KEY_MISMATCH",
    "TIMESTAMP_MISMATCH",
    "SYMBOL_MISMATCH",
    "BUG",
    "UNKNOWN",
)

EXPECTED_REASONS = frozenset({
    "EXPECTED_NO_S55",
    "MATERIALIZED_S40",
    "LEGACY_TRADE",
})

UNEXPECTED_REASONS = frozenset({
    "NO_S55_RECORD",
    "JOIN_KEY_MISMATCH",
    "TIMESTAMP_MISMATCH",
    "SYMBOL_MISMATCH",
    "BUG",
    "UNKNOWN",
})

# Backward-compat aliases used by older tests / callers.
LEGACY_REASON_MAP = {
    "NO_S55_ROW": "NO_S55_RECORD",
    "NO_FOREIGN_KEY": "JOIN_KEY_MISMATCH",
    "JOIN_KEY_ERROR": "JOIN_KEY_MISMATCH",
    "DUPLICATE": "BUG",
}


def _is_stub_s55(s40_type: Any, features_json: Any) -> bool:
    st = str(s40_type or "")
    if st.startswith("research_s55:"):
        return True
    raw = features_json or ""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    return "research_s55_backfill" in str(raw)


def _is_materialized_s40(exit_reason: Any, s40_type: Any) -> bool:
    er = str(exit_reason or "")
    st = str(s40_type or "")
    if er == "research_s40_review" or "research_s40" in er:
        return True
    if st.startswith("research:") or st.startswith("research_s40"):
        return True
    return False


def _is_legacy(exit_reason: Any, s40_type: Any) -> bool:
    er = str(exit_reason or "").lower()
    st = str(s40_type or "").lower()
    if st.startswith("hist:") or st.startswith("legacy"):
        return True
    if "legacy" in er:
        return True
    return False


def diagnose_missing_s55_joins(conn: Any, *, limit: int | None = None) -> dict[str, Any]:
    """
    Classify every lake trade without a *real* (non-stub) S55 feature join.

    Rows with s55_id pointing at research_s55 stubs are treated as effective
    misses and labeled (typically MATERIALIZED_S40) — not as bugs.
    """
    ensure_research_lake_schema(conn)

    # Older/minimal DBs may lack S42 s40 / exit columns — probe safely.
    s42_cols: set[str] = set()
    try:
        s42_cols = {
            str(r[1]) for r in conn.execute(f"PRAGMA table_info({_S42})").fetchall()
        }
    except Exception:
        s42_cols = set()

    def _col(name: str, alias: str | None = None) -> str:
        if name in s42_cols:
            return f"p.{name}" + (f" AS {alias}" if alias else "")
        return f"NULL AS {alias or name}"

    sql = f"""
        SELECT l.trade_id, l.symbol, l.opened_at, l.closed_at, l.s55_id,
               {_col('s40_signal_type')},
               {_col('s40_signal_id')},
               {_col('symbol', 'p_symbol')},
               {_col('created_at')},
               {_col('closed_at', 'p_closed')},
               {_col('exit_reason')},
               f.id AS f_id, f.paper_trade_id AS f_paper_trade_id,
               f.s40_signal_type AS f_s40_type, f.s40_signal_id AS f_s40_id,
               f.symbol AS f_symbol, f.closed_at AS f_closed, f.features_json AS f_feats
        FROM {LAKE_TABLE} l
        LEFT JOIN {_S42} p ON p.id = l.trade_id
        LEFT JOIN {_S55} f ON f.id = l.s55_id
        ORDER BY l.trade_id ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    try:
        rows = conn.execute(sql).fetchall()
    except Exception:
        # Minimal fallback: lake-only
        rows = conn.execute(
            f"""
            SELECT l.trade_id, l.symbol, l.opened_at, l.closed_at, l.s55_id,
                   NULL AS s40_signal_type, NULL AS s40_signal_id, NULL AS p_symbol,
                   NULL AS created_at, NULL AS p_closed, NULL AS exit_reason,
                   f.id AS f_id, f.paper_trade_id AS f_paper_trade_id,
                   f.s40_signal_type AS f_s40_type, f.s40_signal_id AS f_s40_id,
                   f.symbol AS f_symbol, f.closed_at AS f_closed, f.features_json AS f_feats
            FROM {LAKE_TABLE} l
            LEFT JOIN {_S55} f ON f.id = l.s55_id
            ORDER BY l.trade_id ASC
            """
            + (f" LIMIT {int(limit)}" if limit else "")
        ).fetchall()

    by_tid: dict[int, list[dict[str, Any]]] = {}
    by_s40: dict[tuple[str, int], list[dict[str, Any]]] = {}
    by_sym_close: dict[tuple[str, int], list[dict[str, Any]]] = {}
    try:
        for r in conn.execute(
            f"SELECT id, paper_trade_id, s40_signal_type, s40_signal_id, symbol, "
            f"closed_at, features_json FROM {_S55}"
        ).fetchall():
            d = {
                "id": r[0],
                "paper_trade_id": r[1],
                "s40_signal_type": r[2],
                "s40_signal_id": r[3],
                "symbol": r[4],
                "closed_at": r[5],
                "features_json": r[6],
                "is_stub": _is_stub_s55(r[2], r[6]),
            }
            if d["paper_trade_id"] is not None:
                by_tid.setdefault(int(d["paper_trade_id"]), []).append(d)
            if d["s40_signal_type"] is not None and d["s40_signal_id"] is not None:
                by_s40.setdefault((str(d["s40_signal_type"]), int(d["s40_signal_id"])), []).append(d)
            if d.get("symbol") and d.get("closed_at") is not None and not d["is_stub"]:
                bucket = int(d["closed_at"]) // 60
                by_sym_close.setdefault((str(d["symbol"]).upper(), bucket), []).append(d)
    except Exception:
        pass

    reasons: Counter[str] = Counter()
    samples: dict[str, list[int]] = {k: [] for k in REASONS}
    n_raw_null = 0
    n_stub = 0
    n_real_joined = 0

    def _row_get(r: Any, key: str, idx: int) -> Any:
        if hasattr(r, "keys"):
            try:
                return r[key]
            except (KeyError, IndexError):
                return r[idx] if idx < len(r) else None
        return r[idx] if idx < len(r) else None

    for r in rows:
        trade_id = int(_row_get(r, "trade_id", 0))
        sym = str(_row_get(r, "symbol", 1) or "")
        s55_id = _row_get(r, "s55_id", 4)
        s40_t = _row_get(r, "s40_signal_type", 5)
        s40_i = _row_get(r, "s40_signal_id", 6)
        p_sym = _row_get(r, "p_symbol", 7)
        closed = _row_get(r, "closed_at", 3) or _row_get(r, "p_closed", 9)
        exit_reason = _row_get(r, "exit_reason", 10)
        f_id = _row_get(r, "f_id", 11)
        f_paper = _row_get(r, "f_paper_trade_id", 12)
        f_type = _row_get(r, "f_s40_type", 13)
        f_sym = _row_get(r, "f_symbol", 15)
        f_feats = _row_get(r, "f_feats", 17)

        linked_stub = bool(s55_id is not None and f_id is not None and _is_stub_s55(f_type, f_feats))
        linked_real = bool(s55_id is not None and f_id is not None and not _is_stub_s55(f_type, f_feats))
        dangling = bool(s55_id is not None and f_id is None)

        if linked_real:
            n_real_joined += 1
            continue

        if s55_id is None:
            n_raw_null += 1
        if linked_stub:
            n_stub += 1

        # Effective miss — classify
        reason = "UNKNOWN"
        real_hits_tid = [h for h in (by_tid.get(trade_id) or []) if not h.get("is_stub")]
        stub_hits_tid = [h for h in (by_tid.get(trade_id) or []) if h.get("is_stub")]
        hits_s40: list[dict[str, Any]] = []
        if s40_t is not None and s40_i is not None:
            hits_s40 = by_s40.get((str(s40_t), int(s40_i))) or []
        real_hits_s40 = [h for h in hits_s40 if not h.get("is_stub")]

        if dangling:
            reason = "BUG"
        elif linked_stub or stub_hits_tid:
            if _is_materialized_s40(exit_reason, s40_t):
                reason = "MATERIALIZED_S40"
            elif _is_legacy(exit_reason, s40_t):
                reason = "LEGACY_TRADE"
            else:
                reason = "EXPECTED_NO_S55"
        elif real_hits_tid:
            if len(real_hits_tid) > 1:
                reason = "BUG"
            elif s55_id is None:
                reason = "JOIN_KEY_MISMATCH"
            elif f_paper is not None and int(f_paper) != trade_id:
                reason = "JOIN_KEY_MISMATCH"
            else:
                reason = "JOIN_KEY_MISMATCH"
        elif real_hits_s40:
            h = real_hits_s40[0]
            h_sym = str(h.get("symbol") or "").upper()
            want = sym.upper() or str(p_sym or "").upper()
            if h_sym and want and h_sym != want:
                reason = "SYMBOL_MISMATCH"
            else:
                reason = "JOIN_KEY_MISMATCH"
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
            if fuzzy:
                reason = "TIMESTAMP_MISMATCH"
            elif _is_materialized_s40(exit_reason, s40_t):
                reason = "MATERIALIZED_S40"
            elif _is_legacy(exit_reason, s40_t):
                reason = "LEGACY_TRADE"
            elif s40_t is None and exit_reason is None:
                reason = "EXPECTED_NO_S55"
            else:
                # Live-looking trade with no S55 at all
                reason = "NO_S55_RECORD"

        reasons[reason] += 1
        if len(samples[reason]) < 8:
            samples[reason].append(trade_id)

    n_lake = int(conn.execute(f"SELECT COUNT(*) FROM {LAKE_TABLE}").fetchone()[0])
    n_missing = sum(reasons.values())  # effective (no real S55)
    missing_expected = sum(reasons[r] for r in EXPECTED_REASONS)
    missing_unexpected = sum(reasons[r] for r in UNEXPECTED_REASONS)
    pct = round(100.0 * n_missing / n_lake, 4) if n_lake else 0.0
    unexpected_pct = round(100.0 * missing_unexpected / n_lake, 4) if n_lake else 0.0
    expected_pct = round(100.0 * missing_expected / n_lake, 4) if n_lake else 0.0

    verdict = "normal" if missing_unexpected == 0 else "bug"
    table = []
    for reason in REASONS:
        cnt = int(reasons.get(reason) or 0)
        if cnt == 0 and reason not in reasons:
            continue
        table.append({
            "reason": reason,
            "count": cnt,
            "pct": round(100.0 * cnt / n_missing, 4) if n_missing else 0.0,
            "expected": reason in EXPECTED_REASONS,
        })
    # include zero rows for all reasons so CLI table is complete
    table = [
        {
            "reason": reason,
            "count": int(reasons.get(reason) or 0),
            "pct": round(100.0 * int(reasons.get(reason) or 0) / n_missing, 4) if n_missing else 0.0,
            "expected": reason in EXPECTED_REASONS,
        }
        for reason in REASONS
    ]

    return {
        "n_lake": n_lake,
        "n_missing": n_missing,
        "n_missing_raw_null": n_raw_null,
        "n_stub_joined": n_stub,
        "n_real_joined": n_real_joined,
        "missing_pct": pct,
        "missing_expected": missing_expected,
        "missing_unexpected": missing_unexpected,
        "missing_expected_pct": expected_pct,
        "missing_unexpected_pct": unexpected_pct,
        "verdict": verdict,
        "ok": missing_unexpected == 0 or unexpected_pct < 1.0,
        "reasons": dict(reasons),
        "table": table,
        "samples": samples,
    }


def format_s55_join_audit(audit: dict[str, Any]) -> str:
    lines = [
        "S55 JOIN AUDIT V1",
        f"n_lake: {audit.get('n_lake')}",
        f"real_joined: {audit.get('n_real_joined')}",
        f"effective_missing: {audit.get('n_missing')}",
        f"raw_s55_id_null: {audit.get('n_missing_raw_null')}",
        f"stub_joined: {audit.get('n_stub_joined')}",
        f"missing_expected: {audit.get('missing_expected')}",
        f"missing_unexpected: {audit.get('missing_unexpected')}",
        f"verdict: {audit.get('verdict')}",
        "",
        "Причина\tКол-во\t%",
    ]
    for row in audit.get("table") or []:
        if int(row.get("count") or 0) == 0:
            continue
        lines.append(f"{row['reason']}\t{row['count']}\t{row['pct']}")
    if not any(int(r.get("count") or 0) for r in (audit.get("table") or [])):
        lines.append("(none)\t0\t0")
    lines.append("")
    if audit.get("verdict") == "normal":
        lines.append("Это нормально")
    else:
        lines.append("Это баг")
    return "\n".join(lines)


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
        pass

    after = diagnose_missing_s55_joins(conn)
    return {
        "ok": bool(after.get("ok")),
        "before": before,
        "after": after,
        "relinked": relinked,
        "inserted": inserted,
        "patched_lake": patched,
        "elapsed_sec": round(time.time() - t0, 3),
    }


def reconcile_timestamp_mismatches(
    conn: Any,
    *,
    max_delta_sec: int = 60,
) -> dict[str, Any]:
    """
    Auto-reconcile TIMESTAMP_MISMATCH when lake closed_at vs S55 closed_at
    differs by < max_delta_sec. Otherwise leave for report.
    """
    ensure_research_lake_schema(conn)
    max_delta = int(max_delta_sec)
    reconciled = 0
    remaining = 0
    samples_remaining: list[int] = []

    # Index real S55 by symbol
    by_sym: dict[str, list[dict[str, Any]]] = {}
    try:
        for r in conn.execute(
            f"SELECT id, paper_trade_id, symbol, closed_at, s40_signal_type, features_json "
            f"FROM {_S55}"
        ).fetchall():
            if hasattr(r, "keys"):
                f_type, f_feats = r["s40_signal_type"], r["features_json"]
                sym = str(r["symbol"] or "").upper()
                cid = int(r["id"])
                closed = r["closed_at"]
            else:
                f_type, f_feats = r[4], r[5]
                sym = str(r[2] or "").upper()
                cid = int(r[0])
                closed = r[3]
            if _is_stub_s55(f_type, f_feats):
                continue
            try:
                c_i = int(closed) if closed is not None else None
            except Exception:
                c_i = None
            by_sym.setdefault(sym, []).append({"id": cid, "closed_at": c_i})
    except Exception:
        pass

    rows = conn.execute(
        f"""
        SELECT l.trade_id, l.symbol, l.closed_at, l.s55_id,
               f.id AS f_id, f.closed_at AS f_closed, f.features_json, f.s40_signal_type
        FROM {LAKE_TABLE} l
        LEFT JOIN {_S55} f ON f.id = l.s55_id
        """
    ).fetchall()

    for r in rows:
        trade_id = int(r[0] if not hasattr(r, "keys") else r["trade_id"])
        sym = str(r[1] if not hasattr(r, "keys") else r["symbol"] or "").upper()
        l_closed = r[2] if not hasattr(r, "keys") else r["closed_at"]
        s55_id = r[3] if not hasattr(r, "keys") else r["s55_id"]
        f_id = r[4] if not hasattr(r, "keys") else r["f_id"]
        f_closed = r[5] if not hasattr(r, "keys") else r["f_closed"]
        f_type = r[7] if not hasattr(r, "keys") else r["s40_signal_type"]
        f_feats = r[6] if not hasattr(r, "keys") else r["features_json"]

        linked_real = bool(
            s55_id is not None and f_id is not None
            and not _is_stub_s55(f_type, f_feats)
        )
        if linked_real:
            continue

        try:
            lc = int(l_closed) if l_closed is not None else None
        except Exception:
            lc = None
        if lc is None or not sym:
            continue

        best_id = None
        best_delta = None
        for h in by_sym.get(sym, []):
            hc = h.get("closed_at")
            if hc is None:
                continue
            delta = abs(int(hc) - lc)
            if delta <= max_delta:
                if best_delta is None or delta < best_delta:
                    best_delta = delta
                    best_id = int(h["id"])

        if best_id is not None:
            conn.execute(
                f"UPDATE {LAKE_TABLE} SET s55_id = ? WHERE trade_id = ?",
                (best_id, trade_id),
            )
            reconciled += 1
        else:
            # Would classify as TIMESTAMP_MISMATCH if fuzzy within 300s
            fuzzy = False
            for h in by_sym.get(sym, []):
                hc = h.get("closed_at")
                if hc is not None and abs(int(hc) - lc) <= 300:
                    fuzzy = True
                    break
            if fuzzy:
                remaining += 1
                if len(samples_remaining) < 20:
                    samples_remaining.append(trade_id)

    if reconciled:
        try:
            conn.commit()
        except Exception:
            pass

    return {
        "ok": True,
        "max_delta_sec": max_delta,
        "reconciled": reconciled,
        "remaining_timestamp_mismatch": remaining,
        "samples_remaining": samples_remaining,
    }


__all__ = [
    "EXPECTED_REASONS",
    "REASONS",
    "UNEXPECTED_REASONS",
    "diagnose_missing_s55_joins",
    "format_s55_join_audit",
    "reconcile_timestamp_mismatches",
    "repair_s55_joins",
]
