"""Research Lake Builder V2 — streaming batches, preloaded joins, SQL profiling.

Eliminates per-trade SELECT loops (N×N). Research-only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from typing import Any, Callable

from bot.research.market_events.signal_intelligence.feature_store import extract_sample
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.health import (
    research_lake_health_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.indexes import (
    ensure_research_lake_join_indexes,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.profiler import (
    SqlProfiler,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.report import (
    write_lake_artifacts,
    write_lake_profile_report,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    BUILD_TABLE,
    DATASET_VERSION,
    FEATURE_VERSION,
    LAKE_TABLE,
    META_TABLE,
    SCHEMA_VERSION_LAKE,
    ensure_research_lake_schema,
)

logger = logging.getLogger(__name__)

_S42 = "market_events_paper_trades_s42"
_S55 = "market_events_trade_features_s55"
_S56 = "market_events_trade_snapshots_s56"

BATCH_SIZE = 1000


def _row(r: Any) -> dict[str, Any]:
    if hasattr(r, "keys"):
        return {k: r[k] for k in r.keys()}
    return dict(r)


def _parse_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


def _row_hash(payload: dict[str, Any]) -> str:
    blob = _dumps(payload).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _set_meta(conn: Any, key: str, value: str, *, now: int) -> None:
    conn.execute(
        f"""
        INSERT INTO {META_TABLE} (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, now),
    )


def _print_progress(
    print_fn: Callable[..., None],
    *,
    loaded: int,
    joined: int,
    inserted: int,
    updated: int,
    skipped: int,
    total: int | None,
    t0: float,
) -> None:
    elapsed = max(1e-6, time.time() - t0)
    rate = loaded / elapsed
    eta = "—"
    if total and rate > 0 and loaded < total:
        eta = f"{(total - loaded) / rate:.1f}s"
    print_fn(
        f"Loaded={loaded} Joined={joined} Inserted={inserted} "
        f"Updated={updated} Skipped={skipped} ETA={eta} "
        f"({elapsed:.1f}s elapsed, {rate:.0f} rows/s)"
    )


def _preload_s55(conn: Any, profiler: SqlProfiler) -> dict[int, dict[str, Any]]:
    """paper_trade_id -> latest S55 row (highest id wins)."""
    sql = f"SELECT * FROM {_S55} WHERE paper_trade_id IS NOT NULL ORDER BY id ASC"
    t0 = time.perf_counter()
    out: dict[int, dict[str, Any]] = {}
    try:
        rows = conn.execute(sql).fetchall()
        for r in rows:
            d = _row(r)
            tid = d.get("paper_trade_id")
            if tid is None:
                continue
            out[int(tid)] = d
    except Exception as exc:
        logger.warning("research_lake: S55 preload failed: %s", exc)
    profiler.record(sql, rows=len(out), elapsed_sec=time.perf_counter() - t0)
    return out


def _preload_s56(conn: Any, profiler: SqlProfiler) -> dict[int, dict[str, Any]]:
    sql = f"SELECT * FROM {_S56} WHERE paper_trade_id IS NOT NULL ORDER BY id ASC"
    t0 = time.perf_counter()
    out: dict[int, dict[str, Any]] = {}
    try:
        rows = conn.execute(sql).fetchall()
        for r in rows:
            d = _row(r)
            tid = d.get("paper_trade_id")
            if tid is None:
                continue
            out[int(tid)] = d
    except Exception as exc:
        logger.debug("research_lake: S56 preload skipped: %s", exc)
    profiler.record(sql, rows=len(out), elapsed_sec=time.perf_counter() - t0)
    return out


def _preload_g31_by_symbol(conn: Any, profiler: SqlProfiler) -> dict[str, list[dict[str, Any]]]:
    """symbol(upper) -> candidates sorted by created_at."""
    sql = """
        SELECT id, symbol, direction, market_score, confidence, created_at
        FROM market_candidate_g31
    """
    t0 = time.perf_counter()
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    try:
        rows = conn.execute(sql).fetchall()
        for r in rows:
            d = _row(r)
            sym = str(d.get("symbol") or "").upper()
            if not sym:
                continue
            by_sym[sym].append(d)
    except Exception as exc:
        logger.debug("research_lake: G31 preload skipped: %s", exc)
    profiler.record(sql, rows=sum(len(v) for v in by_sym.values()), elapsed_sec=time.perf_counter() - t0)
    return by_sym


def _lookup_g31(
    by_sym: dict[str, list[dict[str, Any]]],
    symbol: str,
    opened_at: int | None,
) -> dict[str, Any] | None:
    if not symbol or not opened_at:
        return None
    cands = by_sym.get(str(symbol).upper()) or []
    if not cands:
        return None
    lo, hi = int(opened_at) - 3600, int(opened_at) + 3600
    best = None
    best_abs = None
    for c in cands:
        ts = c.get("created_at")
        if ts is None:
            continue
        ts = int(ts)
        if ts < lo or ts > hi:
            continue
        d = abs(ts - int(opened_at))
        if best_abs is None or d < best_abs:
            best_abs = d
            best = c
    return best


def _preload_alpha_labels(conn: Any, profiler: SqlProfiler) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = defaultdict(dict)
    queries = (
        (
            """
            SELECT trade_id, paper_trade_id, validation_status, score, rule_id
            FROM market_events_alpha_validations_v2
            """,
            "validation",
            ("trade_id", "paper_trade_id"),
        ),
        (
            """
            SELECT trade_id, cluster, edge_score, status
            FROM market_events_alpha_labels_v1
            """,
            "discovery",
            ("trade_id",),
        ),
    )
    for sql, key, id_cols in queries:
        t0 = time.perf_counter()
        n = 0
        try:
            rows = conn.execute(sql).fetchall()
            for r in rows:
                d = _row(r)
                tid = None
                for col in id_cols:
                    if d.get(col) is not None:
                        tid = int(d[col])
                        break
                if tid is None:
                    continue
                out[tid][key] = d
                n += 1
        except Exception:
            profiler.record(sql, rows=0, elapsed_sec=time.perf_counter() - t0)
            continue
        profiler.record(sql, rows=n, elapsed_sec=time.perf_counter() - t0)
    return dict(out)


def _preload_lake_hashes(conn: Any, profiler: SqlProfiler) -> dict[int, str]:
    sql = f"SELECT trade_id, row_hash FROM {LAKE_TABLE}"
    t0 = time.perf_counter()
    out: dict[int, str] = {}
    try:
        for r in conn.execute(sql).fetchall():
            out[int(r[0])] = str(r[1] or "")
    except Exception:
        pass
    profiler.record(sql, rows=len(out), elapsed_sec=time.perf_counter() - t0)
    return out


def _fetch_optimizer_state(conn: Any, profiler: SqlProfiler) -> dict[str, Any]:
    sql = """
        SELECT key, value FROM market_events_ops_state
        WHERE key LIKE 'optimizer%' OR key LIKE 'g42%'
        LIMIT 20
    """
    t0 = time.perf_counter()
    try:
        r = conn.execute(sql).fetchall()
        out = {str(x[0]): x[1] for x in r} if r else {}
    except Exception:
        out = {}
    profiler.record(sql, rows=len(out), elapsed_sec=time.perf_counter() - t0)
    return out


def _fetch_experiment_state(conn: Any, profiler: SqlProfiler) -> dict[str, Any]:
    sql = """
        SELECT id, name, status FROM market_events_experiments_v1
        ORDER BY id DESC LIMIT 5
    """
    t0 = time.perf_counter()
    try:
        r = conn.execute(sql).fetchall()
        out = {"recent": [_row(x) for x in r]} if r else {}
    except Exception:
        out = {}
    profiler.record(sql, rows=len(out.get("recent") or []), elapsed_sec=time.perf_counter() - t0)
    return out


def _count_closed(conn: Any, profiler: SqlProfiler, *, after_id: int | None = None) -> int:
    sql = f"""
        SELECT COUNT(*) FROM {_S42}
        WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL
    """
    params: tuple[Any, ...] = ()
    if after_id is not None:
        sql += " AND id > ?"
        params = (int(after_id),)
    t0 = time.perf_counter()
    try:
        n = int(conn.execute(sql, params).fetchone()[0])
    except Exception:
        n = 0
    profiler.record(sql, rows=1, elapsed_sec=time.perf_counter() - t0, params=params)
    return n


def _iter_s42_batches(
    conn: Any,
    profiler: SqlProfiler,
    *,
    after_id: int,
    batch_size: int = BATCH_SIZE,
):
    """Stream CLOSED S42 rows with WHERE id > last_id LIMIT batch."""
    last_id = int(after_id)
    sql = f"""
        SELECT * FROM {_S42}
        WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ?
        ORDER BY id ASC
        LIMIT ?
    """
    while True:
        t0 = time.perf_counter()
        rows = [_row(r) for r in conn.execute(sql, (last_id, batch_size)).fetchall()]
        profiler.record(sql, rows=len(rows), elapsed_sec=time.perf_counter() - t0, params=(last_id, batch_size))
        if not rows:
            break
        yield rows
        last_id = int(rows[-1]["id"])
        if len(rows) < batch_size:
            break


def _compose_lake_row(
    trade: dict[str, Any],
    *,
    s55: dict[str, Any] | None,
    s56: dict[str, Any] | None,
    g31: dict[str, Any] | None,
    alpha: dict[str, Any],
    optimizer: dict[str, Any],
    experiment: dict[str, Any],
    now: int,
) -> dict[str, Any]:
    merged = dict(trade)
    if s55:
        for k, v in s55.items():
            if k in ("id",) or k.startswith("f_"):
                continue
            if merged.get(k) is None and v is not None:
                merged[k] = v
        blob = _parse_json(s55.get("features_json"))
        merged.setdefault("features_json", s55.get("features_json"))
        for k, v in blob.items():
            if merged.get(k) is None:
                merged[k] = v

    sample = extract_sample(merged)
    features = {
        k: sample.get(k)
        for k in (
            "rsi", "atr", "atr_pct", "ema20_distance", "ema50_distance", "ema200_distance",
            "vwap_distance", "funding", "funding_delta", "oi_delta", "fear_greed",
            "adx", "macd", "macd_hist", "trend", "stoch_k", "stoch_d", "volatility",
            "volume", "hour", "weekday", "ai_score", "macro_score", "news_score",
            "spread", "confidence",
        )
        if sample.get(k) is not None or merged.get(k) is not None
    }
    for k in list(features):
        if features[k] is None and merged.get(k) is not None:
            features[k] = merged.get(k)

    macro = {
        "funding": _safe_float(merged.get("funding")),
        "oi_delta": _safe_float(merged.get("oi_delta")),
        "fear_greed": _safe_float(merged.get("fear_greed")),
        "btc_dominance": _safe_float(merged.get("btc_dominance")),
        "macro_score": _safe_float(merged.get("macro_score")),
        "etf_flow": _safe_float(merged.get("etf_flow")),
    }
    news = {
        "news_score": _safe_float(merged.get("news_score")),
        "news_category": merged.get("news_category"),
        "ai_score": _safe_float(merged.get("ai_score")),
    }
    patterns = {
        "pattern": sample.get("pattern") or merged.get("pattern"),
        "market_regime": merged.get("market_regime") or (s55 or {}).get("market_regime"),
        "s56": {
            "exit_reason": (s56 or {}).get("exit_reason"),
            "expected_pnl_pct": (s56 or {}).get("expected_pnl_pct"),
        } if s56 else {},
        "g31": g31 or {},
    }

    pnl = _safe_float(trade.get("pnl_usd"))
    if pnl is None:
        pnl = _safe_float(trade.get("pnl_pct"))
    if pnl is None and s55:
        pnl = _safe_float(s55.get("pnl_usd")) or _safe_float(s55.get("pnl_pct"))

    exit_px = _safe_float(trade.get("exit")) or _safe_float(trade.get("exit_price"))
    if exit_px is None and s56:
        exit_px = _safe_float(s56.get("exit_price"))

    payload = {
        "trade_id": int(trade["id"]),
        "symbol": str(trade.get("symbol") or "").upper(),
        "direction": str(trade.get("direction") or "").upper(),
        "entry": _safe_float(trade.get("entry")),
        "exit": exit_px,
        "result": trade.get("result") or (s55 or {}).get("result"),
        "pnl": pnl,
        "pnl_pct": _safe_float(trade.get("pnl_pct")),
        "gate": trade.get("gate_decision") or (s55 or {}).get("gate_decision"),
        "confidence": _safe_float(merged.get("confidence") or merged.get("decision_confidence")),
        "regime": merged.get("market_regime") or (s55 or {}).get("market_regime"),
        "features": features,
        "macro": macro,
        "news": news,
        "patterns": patterns,
        "alpha_labels": alpha,
        "optimizer_state": optimizer,
        "experiment_state": experiment,
        "feature_version": FEATURE_VERSION,
        "dataset_version": DATASET_VERSION,
        "schema_version": SCHEMA_VERSION_LAKE,
        "s55_id": int(s55["id"]) if s55 and s55.get("id") is not None else None,
        "s56_id": int(s56["id"]) if s56 and s56.get("id") is not None else None,
        "g31_candidate_id": int(g31["id"]) if g31 and g31.get("id") is not None else None,
        "status": trade.get("status"),
        "closed_at": trade.get("closed_at"),
        "opened_at": trade.get("created_at") or trade.get("opened_at"),
    }
    payload["row_hash"] = _row_hash(payload)
    payload["updated_at"] = now
    payload["built_at"] = now
    return payload


_UPSERT_SQL = f"""
INSERT INTO {LAKE_TABLE} (
  trade_id, symbol, direction, entry, exit, result, pnl, pnl_pct,
  gate, confidence, regime,
  features_json, macro_json, news_json, patterns_json,
  alpha_labels_json, optimizer_state_json, experiment_state_json,
  feature_version, dataset_version, schema_version,
  s55_id, s56_id, g31_candidate_id, status, closed_at, opened_at,
  updated_at, built_at, row_hash
) VALUES (
  ?,?,?,?,?,?,?,?,
  ?,?,?,
  ?,?,?,?,
  ?,?,?,
  ?,?,?,
  ?,?,?,?,?,?,
  ?,?,?
)
ON CONFLICT(trade_id) DO UPDATE SET
  symbol=excluded.symbol,
  direction=excluded.direction,
  entry=excluded.entry,
  exit=excluded.exit,
  result=excluded.result,
  pnl=excluded.pnl,
  pnl_pct=excluded.pnl_pct,
  gate=excluded.gate,
  confidence=excluded.confidence,
  regime=excluded.regime,
  features_json=excluded.features_json,
  macro_json=excluded.macro_json,
  news_json=excluded.news_json,
  patterns_json=excluded.patterns_json,
  alpha_labels_json=excluded.alpha_labels_json,
  optimizer_state_json=excluded.optimizer_state_json,
  experiment_state_json=excluded.experiment_state_json,
  feature_version=excluded.feature_version,
  dataset_version=excluded.dataset_version,
  schema_version=excluded.schema_version,
  s55_id=excluded.s55_id,
  s56_id=excluded.s56_id,
  g31_candidate_id=excluded.g31_candidate_id,
  status=excluded.status,
  closed_at=excluded.closed_at,
  opened_at=excluded.opened_at,
  updated_at=excluded.updated_at,
  built_at=excluded.built_at,
  row_hash=excluded.row_hash
"""


def _row_params(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["trade_id"], row["symbol"], row["direction"], row["entry"], row["exit"],
        row["result"], row["pnl"], row["pnl_pct"],
        row["gate"], row["confidence"], row["regime"],
        _dumps(row["features"]), _dumps(row["macro"]), _dumps(row["news"]), _dumps(row["patterns"]),
        _dumps(row["alpha_labels"]), _dumps(row["optimizer_state"]), _dumps(row["experiment_state"]),
        row["feature_version"], row["dataset_version"], row["schema_version"],
        row["s55_id"], row["s56_id"], row["g31_candidate_id"], row["status"],
        row["closed_at"], row["opened_at"],
        row["updated_at"], row["built_at"], row["row_hash"],
    )


def build_research_lake_v1(
    conn: Any,
    *,
    full: bool = True,
    write_reports: bool = True,
    batch_size: int = BATCH_SIZE,
    print_fn: Callable[..., None] | None = print,
    profile: bool = True,
    materialize_s40: bool = True,
) -> dict[str, Any]:
    """Stream S42 in batches; join via preloaded dicts; commit every batch."""
    t0 = time.time()
    print_fn = print_fn or (lambda *_a, **_k: None)
    profiler = SqlProfiler(slow_threshold_sec=1.0, print_each=bool(profile), _print_fn=print_fn)
    # Do not wrap conn.execute: SELECT cost is in fetch/iteration; we time
    # execute+fetchall together in preload/stream helpers (accurate rows+ms).

    t_schema = time.perf_counter()
    ensure_research_lake_schema(conn)
    profiler.record("-- ensure_research_lake_schema", rows=0, elapsed_sec=time.perf_counter() - t_schema)

    t_idx = time.perf_counter()
    indexes_added = ensure_research_lake_join_indexes(conn)
    profiler.record(
        "-- ensure_research_lake_join_indexes",
        rows=len(indexes_added),
        elapsed_sec=time.perf_counter() - t_idx,
    )

    materialize_stats: dict[str, Any] = {"skipped": True}
    if materialize_s40:
        from bot.research.market_events.signal_intelligence.research_lake_v1.materialize import (
            materialize_closed_from_s40_reviews,
        )

        print_fn("Materializing missing CLOSED S42 rows from S40 reviews…")
        t_mat = time.perf_counter()
        materialize_stats = materialize_closed_from_s40_reviews(conn)
        profiler.record(
            "-- materialize_closed_from_s40_reviews",
            rows=int(materialize_stats.get("inserted") or 0),
            elapsed_sec=time.perf_counter() - t_mat,
        )
        print_fn(
            f"Materialize done: inserted={materialize_stats.get('inserted')} "
            f"closed {materialize_stats.get('before_closed')}→{materialize_stats.get('after_closed')} "
            f"({materialize_stats.get('elapsed_sec')}s)"
        )
        # After expanding S42, always do a full lake pass for coverage.
        if int(materialize_stats.get("inserted") or 0) > 0:
            full = True

    now = int(time.time())
    mode = "full" if full else "incremental"

    after_id = 0
    if not full:
        t_max = time.perf_counter()
        try:
            row = conn.execute(f"SELECT MAX(trade_id) FROM {LAKE_TABLE}").fetchone()
            after_id = int(row[0]) if row and row[0] is not None else 0
        except Exception:
            after_id = 0
        profiler.record(
            f"SELECT MAX(trade_id) FROM {LAKE_TABLE}",
            rows=1,
            elapsed_sec=time.perf_counter() - t_max,
        )

    # --- Preload lookup tables (no SELECT inside trade loop) ---
    print_fn("Preloading join tables into memory…")
    t_pre = time.time()
    s55_by_tid = _preload_s55(conn, profiler)
    s56_by_tid = _preload_s56(conn, profiler)
    g31_by_sym = _preload_g31_by_symbol(conn, profiler)
    alpha_by_tid = _preload_alpha_labels(conn, profiler)
    # Full: hash-compare all rows. Incremental: only id > last_id (hashes unused).
    lake_hashes = _preload_lake_hashes(conn, profiler) if full else {}
    optimizer = _fetch_optimizer_state(conn, profiler)
    experiment = _fetch_experiment_state(conn, profiler)
    total = _count_closed(conn, profiler, after_id=None if full else after_id)
    print_fn(
        f"Preload done in {time.time() - t_pre:.2f}s "
        f"(s55={len(s55_by_tid)} s56={len(s56_by_tid)} "
        f"g31_syms={len(g31_by_sym)} alpha={len(alpha_by_tid)} "
        f"lake_hashes={len(lake_hashes)} total_closed≈{total})"
    )

    inserted = updated = skipped = 0
    loaded = joined = 0
    pending_params: list[tuple[Any, ...]] = []
    pending_actions: list[str] = []

    def _flush() -> None:
        nonlocal inserted, updated, skipped, pending_params, pending_actions
        if not pending_params:
            return
        t_ins = time.perf_counter()
        conn.executemany(_UPSERT_SQL, pending_params)
        profiler.record(
            _UPSERT_SQL,
            rows=len(pending_params),
            elapsed_sec=time.perf_counter() - t_ins,
            params=f"batch[{len(pending_params)}]",
        )
        for a in pending_actions:
            if a == "inserted":
                inserted += 1
            elif a == "updated":
                updated += 1
            else:
                skipped += 1
        try:
            conn.commit()
        except Exception:
            pass
        pending_params = []
        pending_actions = []

    for batch in _iter_s42_batches(conn, profiler, after_id=after_id, batch_size=batch_size):
        for trade in batch:
            loaded += 1
            tid = int(trade["id"])
            s55 = s55_by_tid.get(tid)
            s56 = s56_by_tid.get(tid)
            opened = trade.get("created_at") or trade.get("opened_at")
            g31 = _lookup_g31(g31_by_sym, str(trade.get("symbol") or ""), opened)
            alpha = alpha_by_tid.get(tid) or {}
            joined += 1
            composed = _compose_lake_row(
                trade,
                s55=s55,
                s56=s56,
                g31=g31,
                alpha=alpha,
                optimizer=optimizer,
                experiment=experiment,
                now=now,
            )
            prev_hash = lake_hashes.get(tid)
            if prev_hash is not None and prev_hash == composed["row_hash"]:
                skipped += 1
                continue
            action = "updated" if prev_hash is not None else "inserted"
            pending_params.append(_row_params(composed))
            pending_actions.append(action)
            lake_hashes[tid] = composed["row_hash"]

        _flush()
        _print_progress(
            print_fn,
            loaded=loaded,
            joined=joined,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            total=total,
            t0=t0,
        )

    _flush()

    # Lightweight health (still profiled)
    health = research_lake_health_v1(conn)
    conn.execute(
        f"""
        INSERT INTO {BUILD_TABLE} (
          build_ts, mode, rows_seen, rows_inserted, rows_updated, rows_skipped,
          health_json, dataset_version, feature_version, schema_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now, mode, loaded, inserted, updated, skipped,
            _dumps(health), DATASET_VERSION, FEATURE_VERSION, SCHEMA_VERSION_LAKE, now,
        ),
    )
    _set_meta(conn, "dataset_version", DATASET_VERSION, now=now)
    _set_meta(conn, "feature_version", FEATURE_VERSION, now=now)
    _set_meta(conn, "schema_version", SCHEMA_VERSION_LAKE, now=now)
    _set_meta(conn, "last_build_ts", str(now), now=now)
    _set_meta(conn, "builder_version", "v2-streaming", now=now)
    try:
        conn.commit()
    except Exception:
        pass

    elapsed = round(time.time() - t0, 3)
    explain = profiler.explain_slow(conn)
    profile_summary = profiler.summary()
    result = {
        "ok": True,
        "mode": mode,
        "builder_version": "v2-streaming",
        "rows_seen": loaded,
        "rows_inserted": inserted,
        "rows_updated": updated,
        "rows_skipped": skipped,
        "rows_joined": joined,
        "batch_size": batch_size,
        "indexes_ensured": indexes_added,
        "materialize": materialize_stats,
        "n_s42_closed": (health or {}).get("n_s42_closed"),
        "coverage_pct": (health or {}).get("coverage_pct"),
        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "schema_version": SCHEMA_VERSION_LAKE,
        "health": health,
        "elapsed_sec": elapsed,
        "profile": profile_summary,
        "explain_slow": explain,
        "read_only_sources": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "paper_live_path_unchanged": True,
        "execution_unchanged": True,
        "no_select_in_trade_loop": True,
    }
    if write_reports:
        paths = write_lake_artifacts(result)
        paths.update(write_lake_profile_report(result))
        result["paths"] = paths
        try:
            from pathlib import Path

            result["report_markdown"] = Path(paths["report_md"]).read_text(encoding="utf-8")
            result["profile_markdown"] = Path(paths["profile_md"]).read_text(encoding="utf-8")
        except Exception:
            result["report_markdown"] = ""
            result["profile_markdown"] = ""
    return result


__all__ = ["BATCH_SIZE", "build_research_lake_v1"]
