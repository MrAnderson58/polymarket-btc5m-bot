"""Incremental Research Lake builder (S42 hub + joins)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from bot.research.market_events.signal_intelligence.feature_store import extract_sample
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.health import (
    research_lake_health_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.report import (
    write_lake_artifacts,
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


def _fetch_s42_closed(conn: Any, *, since_id: int | None = None) -> list[dict[str, Any]]:
    sql = f"""
        SELECT * FROM {_S42}
        WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL
    """
    params: tuple[Any, ...] = ()
    if since_id is not None:
        sql += " AND id > ?"
        params = (int(since_id),)
    sql += " ORDER BY id ASC"
    try:
        return [_row(r) for r in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        logger.warning("research_lake: S42 load failed: %s", exc)
        return []


def _fetch_s55_by_trade(conn: Any, trade_id: int) -> dict[str, Any] | None:
    try:
        r = conn.execute(
            f"SELECT * FROM {_S55} WHERE paper_trade_id = ? ORDER BY id DESC LIMIT 1",
            (trade_id,),
        ).fetchone()
        return _row(r) if r else None
    except Exception:
        return None


def _fetch_s56_by_trade(conn: Any, trade_id: int) -> dict[str, Any] | None:
    try:
        r = conn.execute(
            f"SELECT * FROM {_S56} WHERE paper_trade_id = ? ORDER BY id DESC LIMIT 1",
            (trade_id,),
        ).fetchone()
        return _row(r) if r else None
    except Exception:
        return None


def _fetch_g31_candidate(conn: Any, symbol: str, opened_at: int | None) -> dict[str, Any] | None:
    if not symbol or not opened_at:
        return None
    try:
        r = conn.execute(
            """
            SELECT id, symbol, direction, market_score, confidence, created_at
            FROM market_candidate_g31
            WHERE UPPER(symbol) = UPPER(?)
              AND created_at BETWEEN ? AND ?
            ORDER BY ABS(created_at - ?) ASC
            LIMIT 1
            """,
            (symbol, int(opened_at) - 3600, int(opened_at) + 3600, int(opened_at)),
        ).fetchone()
        return _row(r) if r else None
    except Exception:
        return None


def _fetch_alpha_labels(conn: Any, trade_id: int) -> dict[str, Any]:
    """Best-effort labels from Alpha Validation / Discovery tables if present."""
    out: dict[str, Any] = {}
    for sql, key in (
        (
            """
            SELECT validation_status, score, rule_id
            FROM market_events_alpha_validations_v2
            WHERE trade_id = ? OR paper_trade_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            "validation",
        ),
        (
            """
            SELECT cluster, edge_score, status
            FROM market_events_alpha_labels_v1
            WHERE trade_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            "discovery",
        ),
    ):
        try:
            params = (trade_id, trade_id) if "paper_trade_id" in sql else (trade_id,)
            r = conn.execute(sql, params).fetchone()
            if r:
                out[key] = _row(r)
        except Exception:
            continue
    return out


def _fetch_optimizer_state(conn: Any) -> dict[str, Any]:
    try:
        r = conn.execute(
            """
            SELECT key, value FROM market_events_ops_state
            WHERE key LIKE 'optimizer%' OR key LIKE 'g42%'
            LIMIT 20
            """
        ).fetchall()
        return {str(x[0]): x[1] for x in r} if r else {}
    except Exception:
        return {}


def _fetch_experiment_state(conn: Any) -> dict[str, Any]:
    try:
        r = conn.execute(
            """
            SELECT id, name, status FROM market_events_experiments_v1
            ORDER BY id DESC LIMIT 5
            """
        ).fetchall()
        return {"recent": [_row(x) for x in r]} if r else {}
    except Exception:
        return {}


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


def _upsert_lake_row(conn: Any, row: dict[str, Any]) -> str:
    """Insert or update; return 'inserted'|'updated'|'skipped'."""
    existing = conn.execute(
        f"SELECT row_hash, updated_at FROM {LAKE_TABLE} WHERE trade_id = ?",
        (row["trade_id"],),
    ).fetchone()
    if existing and str(existing[0] or "") == str(row["row_hash"]):
        return "skipped"

    conn.execute(
        f"""
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
        """,
        (
            row["trade_id"], row["symbol"], row["direction"], row["entry"], row["exit"],
            row["result"], row["pnl"], row["pnl_pct"],
            row["gate"], row["confidence"], row["regime"],
            _dumps(row["features"]), _dumps(row["macro"]), _dumps(row["news"]), _dumps(row["patterns"]),
            _dumps(row["alpha_labels"]), _dumps(row["optimizer_state"]), _dumps(row["experiment_state"]),
            row["feature_version"], row["dataset_version"], row["schema_version"],
            row["s55_id"], row["s56_id"], row["g31_candidate_id"], row["status"],
            row["closed_at"], row["opened_at"],
            row["updated_at"], row["built_at"], row["row_hash"],
        ),
    )
    return "updated" if existing else "inserted"


def build_research_lake_v1(
    conn: Any,
    *,
    full: bool = False,
    write_reports: bool = True,
) -> dict[str, Any]:
    """Incrementally (or fully) rebuild the canonical Research Lake."""
    t0 = time.time()
    ensure_research_lake_schema(conn)
    now = int(time.time())

    since_id: int | None = None
    mode = "full" if full else "incremental"
    if not full:
        try:
            row = conn.execute(f"SELECT MAX(trade_id) FROM {LAKE_TABLE}").fetchone()
            since_id = int(row[0]) if row and row[0] is not None else None
            # Re-scan last few for updates: drop since_id and filter by missing/outdated instead
        except Exception:
            since_id = None

    if full:
        trades = _fetch_s42_closed(conn)
    else:
        # Incremental: new ids OR not present in lake
        all_closed = _fetch_s42_closed(conn)
        existing_ids = {
            int(r[0])
            for r in conn.execute(f"SELECT trade_id FROM {LAKE_TABLE}").fetchall()
        }
        trades = [t for t in all_closed if int(t["id"]) not in existing_ids]
        # Also refresh recently updated closed trades (by s42.updated_at)
        try:
            recent = conn.execute(
                f"""
                SELECT p.* FROM {_S42} p
                JOIN {LAKE_TABLE} l ON l.trade_id = p.id
                WHERE p.status='CLOSED' AND p.pnl_pct IS NOT NULL
                  AND COALESCE(p.updated_at, p.closed_at, 0) > COALESCE(l.updated_at, 0)
                """
            ).fetchall()
            seen = {int(t["id"]) for t in trades}
            for r in recent:
                d = _row(r)
                if int(d["id"]) not in seen:
                    trades.append(d)
        except Exception:
            pass

    optimizer = _fetch_optimizer_state(conn)
    experiment = _fetch_experiment_state(conn)

    inserted = updated = skipped = 0
    for trade in trades:
        tid = int(trade["id"])
        s55 = _fetch_s55_by_trade(conn, tid)
        s56 = _fetch_s56_by_trade(conn, tid)
        opened = trade.get("created_at") or trade.get("opened_at")
        g31 = _fetch_g31_candidate(conn, str(trade.get("symbol") or ""), opened)
        alpha = _fetch_alpha_labels(conn, tid)
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
        action = _upsert_lake_row(conn, composed)
        if action == "inserted":
            inserted += 1
        elif action == "updated":
            updated += 1
        else:
            skipped += 1

    health = research_lake_health_v1(conn)
    conn.execute(
        f"""
        INSERT INTO {BUILD_TABLE} (
          build_ts, mode, rows_seen, rows_inserted, rows_updated, rows_skipped,
          health_json, dataset_version, feature_version, schema_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now, mode, len(trades), inserted, updated, skipped,
            _dumps(health), DATASET_VERSION, FEATURE_VERSION, SCHEMA_VERSION_LAKE, now,
        ),
    )
    _set_meta(conn, "dataset_version", DATASET_VERSION, now=now)
    _set_meta(conn, "feature_version", FEATURE_VERSION, now=now)
    _set_meta(conn, "schema_version", SCHEMA_VERSION_LAKE, now=now)
    _set_meta(conn, "last_build_ts", str(now), now=now)
    try:
        conn.commit()
    except Exception:
        pass

    result = {
        "ok": True,
        "mode": mode,
        "rows_seen": len(trades),
        "rows_inserted": inserted,
        "rows_updated": updated,
        "rows_skipped": skipped,
        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "schema_version": SCHEMA_VERSION_LAKE,
        "health": health,
        "elapsed_sec": round(time.time() - t0, 3),
        "read_only_sources": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
    }
    if write_reports:
        result["paths"] = write_lake_artifacts(result)
        try:
            from pathlib import Path

            result["report_markdown"] = Path(result["paths"]["report_md"]).read_text(encoding="utf-8")
        except Exception:
            result["report_markdown"] = ""
    return result


__all__ = ["build_research_lake_v1"]
