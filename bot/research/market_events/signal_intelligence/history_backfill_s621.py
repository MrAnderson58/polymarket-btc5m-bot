"""History backfill — copy completed trades into research S56 snapshots.

Data migration only. No strategy / indicator / report logic changes.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from bot.research.market_events.db import execute_with_retry

logger = logging.getLogger(__name__)

_SNAP = "market_events_trade_snapshots_s56"

# Known completed-trade sources inside trades.db (in priority / discovery order).
TRADES_DB_SOURCES: tuple[dict[str, Any], ...] = (
    {"table": "early_reversion_v2_trades", "status_in": ("closed",), "label": "er_v2"},
    {"table": "early_reversion_v3_trades", "status_in": ("closed",), "label": "er_v3"},
    {"table": "early_reversion_v25_trades", "status_in": ("closed",), "label": "er_v25"},
    {"table": "early_reversion_trades", "status_in": ("closed",), "label": "er_v1"},
    {"table": "yes_c_shadow_trades", "status_in": ("closed",), "label": "yes_c_shadow"},
    {"table": "v4_shadow_trades", "status_in": ("closed",), "label": "v4_shadow"},
    {"table": "bidirectional_shadow_trades", "status_in": ("closed",), "label": "bidir_shadow"},
    {"table": "bidirectional_shadow_v12_trades", "status_in": ("closed",), "label": "bidir_v12"},
    {"table": "virtual_trades", "status_in": ("settled", "closed"), "label": "virtual"},
)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _pnl_predicate(cols: set[str]) -> str | None:
    """SQL fragment requiring a usable PnL (usd or percent proxy)."""
    parts: list[str] = []
    if "pnl_usdc" in cols:
        parts.append("pnl_usdc IS NOT NULL")
    if "pnl" in cols:
        parts.append("pnl IS NOT NULL")
    if "pnl_pct" in cols:
        parts.append("pnl_pct IS NOT NULL")
    if "pnl_percent" in cols:
        parts.append("pnl_percent IS NOT NULL")
    if "realized_profit_pct" in cols:
        parts.append("realized_profit_pct IS NOT NULL")
    if not parts:
        return None
    return "(" + " OR ".join(parts) + ")"

LIVE_PAPER_SOURCE = {
    "table": "market_events_paper_trades_s42",
    "status_in": ("CLOSED",),
    "label": "s42_paper",
}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def default_trades_db_path() -> Path:
    raw = os.getenv("DATABASE_PATH") or os.getenv("TRADES_DATABASE_PATH")
    if raw:
        return Path(raw)
    return _repo_root() / "data" / "trades.db"


def default_live_db_path() -> Path:
    from bot.research.market_events.config import DEFAULT_DB_PATH

    return Path(os.getenv("MARKET_EVENTS_DATABASE_PATH", str(DEFAULT_DB_PATH)))


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        iv = int(v)
        # Heuristic: reject tiny / nonsense timestamps
        if iv > 1_000_000_000:
            return iv
        if 0 < iv < 1_000_000_000:
            # likely relative / garbage — keep if looks like unix ms
            if iv > 1_000_000_000_000:
                return iv // 1000
            return None
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            return int(datetime.strptime(s[:26], fmt).timestamp())
        except ValueError:
            continue
    return None


def _symbol_from_slug(slug: Any) -> str:
    s = str(slug or "").strip().lower()
    if not s:
        return "BTC"
    head = s.split("-")[0].upper()
    if head in ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "AVAX", "LINK", "PEPE"):
        return head
    if "btc" in s:
        return "BTC"
    return head or "BTC"


def _direction_from_side(side: Any) -> str:
    s = str(side or "").strip().upper()
    if s in ("LONG", "SHORT"):
        return s
    if s in ("YES", "UP", "BUY"):
        return "LONG"
    if s in ("NO", "DOWN", "SELL"):
        return "SHORT"
    return s or "LONG"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def discover_trade_sources(
    *,
    trades_db: Path | None = None,
    live_db: Path | None = None,
) -> list[dict[str, Any]]:
    """List available completed-trade sources with row counts."""
    found: list[dict[str, Any]] = []
    trades_db = trades_db or default_trades_db_path()
    live_db = live_db or default_live_db_path()

    if trades_db.exists() and trades_db.stat().st_size > 0:
        conn = sqlite3.connect(f"file:{trades_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            for src in TRADES_DB_SOURCES:
                table = src["table"]
                if not _table_exists(conn, table):
                    continue
                statuses = tuple(src["status_in"])
                placeholders = ",".join("?" * len(statuses))
                # Prefer rows with a PnL column when present
                cols = _table_columns(conn, table)
                if "status" not in cols:
                    continue
                sql = f"SELECT COUNT(*) AS n FROM {table} WHERE lower(status) IN ({placeholders})"
                params: list[Any] = [s.lower() for s in statuses]
                pred = _pnl_predicate(cols)
                if pred:
                    sql += f" AND {pred}"
                n = int(conn.execute(sql, params).fetchone()["n"])
                found.append({
                    "db": str(trades_db.resolve()),
                    "table": table,
                    "label": src["label"],
                    "signal_type": f"hist:{src['label']}",
                    "completed": n,
                    "kind": "trades_db",
                })
        finally:
            conn.close()

    if live_db.exists() and live_db.stat().st_size > 0:
        conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            table = LIVE_PAPER_SOURCE["table"]
            if _table_exists(conn, table):
                n = int(conn.execute(
                    f"""
                    SELECT COUNT(*) AS n FROM {table}
                    WHERE status = 'CLOSED' AND pnl_usd IS NOT NULL
                    """,
                ).fetchone()["n"])
                found.append({
                    "db": str(live_db.resolve()),
                    "table": table,
                    "label": LIVE_PAPER_SOURCE["label"],
                    "signal_type": "s42",
                    "completed": n,
                    "kind": "live_paper",
                })
        finally:
            conn.close()

    return found


def _load_trade_features_index(conn: sqlite3.Connection) -> dict[tuple[str, int], dict[str, Any]]:
    """Map (source_table, trade_id) → feature row from trades.db trade_features."""
    if not _table_exists(conn, "trade_features"):
        return {}
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for r in conn.execute("SELECT * FROM trade_features"):
        d = dict(r)
        try:
            tid = int(d.get("trade_id") or 0)
        except (TypeError, ValueError):
            continue
        src = str(d.get("source_table") or "")
        if tid and src:
            out[(src, tid)] = d
    return out


def _iter_trades_db_rows(
    conn: sqlite3.Connection,
    src: dict[str, Any],
    features_idx: dict[tuple[str, int], dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    table = src["table"]
    statuses = tuple(s.lower() for s in src["status_in"])
    placeholders = ",".join("?" * len(statuses))
    cols = _table_columns(conn, table)
    sql = f"SELECT * FROM {table} WHERE lower(status) IN ({placeholders})"
    params: list[Any] = list(statuses)
    pred = _pnl_predicate(cols)
    if pred:
        sql += f" AND {pred}"
    for r in conn.execute(sql, params):
        d = dict(r)
        tid = int(d.get("id") or 0)
        feat = features_idx.get((table, tid), {})
        yield _normalize_trades_db_row(d, src=src, features=feat)


def _normalize_trades_db_row(
    d: dict[str, Any],
    *,
    src: dict[str, Any],
    features: dict[str, Any],
) -> dict[str, Any]:
    entry_ts = _parse_ts(d.get("entry_ts")) or _parse_ts(d.get("created_at")) or 0
    closed_ts = (
        _parse_ts(d.get("closed_at"))
        or _parse_ts(d.get("settled_at"))
        or _parse_ts(d.get("end_ts"))
        or entry_ts
    )
    pnl_usd = _safe_float(d.get("pnl_usdc"))
    if pnl_usd is None:
        pnl_usd = _safe_float(d.get("pnl"))
    if pnl_usd is None and features:
        pnl_usd = _safe_float(features.get("pnl_usdc")) or _safe_float(features.get("pnl"))

    pnl_pct = _safe_float(d.get("pnl_percent"))
    if pnl_pct is None:
        pnl_pct = _safe_float(d.get("pnl_pct"))
    if pnl_pct is None:
        pnl_pct = _safe_float(d.get("realized_profit_pct"))
    if pnl_pct is None and features:
        pnl_pct = _safe_float(features.get("pnl"))

    # Shadows without dollar PnL: $1 notional → usd = percent / 100
    if pnl_usd is None and pnl_pct is not None:
        pnl_usd = float(pnl_pct) / 100.0
    size = _safe_float(d.get("size_usdc"))
    if size is not None and size > 0 and pnl_pct is not None and d.get("pnl_usdc") is None and d.get("pnl") is None:
        pnl_usd = size * float(pnl_pct) / 100.0

    hold = _safe_float(d.get("holding_time_seconds"))
    if hold is None:
        hold = _safe_float(d.get("holding_time"))
    if hold is None and features:
        hold = _safe_float(features.get("holding_time")) or _safe_float(features.get("seconds_open"))
    if hold is None and entry_ts and closed_ts and closed_ts >= entry_ts:
        hold = float(closed_ts - entry_ts)

    entry = _safe_float(d.get("entry_price")) or _safe_float(d.get("entry_ask"))
    exit_price = _safe_float(d.get("exit_price"))
    conf = (
        _safe_float(d.get("entry_probability"))
        or _safe_float(d.get("entry_score"))
        or _safe_float(d.get("entry_confidence"))
    )

    regime = features.get("regime_label")
    mae = _safe_float(features.get("mae"))
    mfe = _safe_float(features.get("mfe"))
    spread = _safe_float(features.get("spread"))
    vol = _safe_float(features.get("volatility_60s")) or _safe_float(features.get("volatility_30s"))

    strategy = str(d.get("strategy_name") or src["label"])
    symbol = _symbol_from_slug(d.get("market_slug"))
    direction = _direction_from_side(d.get("side"))
    exit_reason = d.get("exit_reason") or features.get("exit_reason")

    trailing = 1 if (
        int(d.get("trailing_active") or 0) == 1
        or str(exit_reason or "").upper() in ("TRAILING_STOP", "TRAILING")
        or int(features.get("is_trailing") or 0) == 1
    ) else 0

    hour = None
    weekday = None
    if entry_ts:
        dt = datetime.fromtimestamp(entry_ts)
        hour = dt.hour
        weekday = dt.weekday()

    snap = {
        "paper_trade_id": int(d.get("id") or 0) or None,
        "s40_signal_type": f"hist:{src['label']}",
        "s40_signal_id": int(d.get("id") or 0),
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "exit_price": exit_price,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "duration_sec": int(hold) if hold is not None else None,
        "exit_reason": exit_reason,
        "tp1": None,
        "tp2": None,
        "trailing": trailing,
        "ai_score": conf,
        "expected_pnl_pct": None,
        "funding": None,
        "oi_delta": None,
        "etf_flow": None,
        "fear_greed": None,
        "macro_score": None,
        "news_score": None,
        "market_score": None,
        "volatility": vol,
        "atr": None,
        "volume": None,
        "trend": None,
        "vwap": None,
        "spread": spread,
        "timestamp": entry_ts or closed_ts,
        "hour": hour,
        "weekday": weekday,
        "market_regime": regime,
        "created_at": closed_ts or entry_ts or int(time.time()),
        "_extra": {
            "source_db": "trades.db",
            "source_table": src["table"],
            "strategy": strategy,
            "market_slug": d.get("market_slug"),
            "entry_ts": entry_ts,
            "exit_ts": closed_ts,
            "mae": mae,
            "mfe": mfe,
            "status": d.get("status"),
            "features": {k: features.get(k) for k in features if k not in ("id",)} if features else {},
        },
    }
    return snap


def _iter_live_paper_rows(conn: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    table = LIVE_PAPER_SOURCE["table"]
    if not _table_exists(conn, table):
        return
    # Features from S55 if present
    feat_by: dict[tuple[str, int], dict[str, Any]] = {}
    if _table_exists(conn, "market_events_trade_features_s55"):
        for r in conn.execute("SELECT * FROM market_events_trade_features_s55"):
            d = dict(r)
            key = (str(d.get("s40_signal_type") or ""), int(d.get("s40_signal_id") or 0))
            feat_by[key] = d

    for r in conn.execute(
        f"""
        SELECT * FROM {table}
        WHERE status = 'CLOSED' AND pnl_usd IS NOT NULL
        """,
    ):
        d = dict(r)
        s_type = str(d.get("s40_signal_type") or "s42")
        s_id = int(d.get("s40_signal_id") or d.get("id") or 0)
        feat = feat_by.get((s_type, s_id), {})
        entry_ts = _parse_ts(d.get("created_at")) or 0
        closed_ts = _parse_ts(d.get("closed_at")) or entry_ts
        hour = feat.get("hour")
        weekday = feat.get("weekday")
        if hour is None and entry_ts:
            dt = datetime.fromtimestamp(entry_ts)
            hour, weekday = dt.hour, dt.weekday()
        snap = {
            "paper_trade_id": int(d.get("id") or 0) or None,
            "s40_signal_type": s_type if s_type.startswith("hist:") else f"hist:s42:{s_type}",
            "s40_signal_id": s_id or int(d.get("id") or 0),
            "symbol": str(d.get("symbol") or feat.get("symbol") or "BTC"),
            "direction": str(d.get("direction") or feat.get("direction") or "LONG").upper(),
            "entry": _safe_float(d.get("entry")),
            "exit_price": _safe_float(d.get("exit_price")),
            "pnl_usd": _safe_float(d.get("pnl_usd")),
            "pnl_pct": _safe_float(d.get("pnl_pct")),
            "duration_sec": int(d.get("holding_seconds") or feat.get("duration_sec") or 0) or None,
            "exit_reason": d.get("exit_reason") or feat.get("exit_reason"),
            "tp1": _safe_float(d.get("tp1")),
            "tp2": _safe_float(d.get("tp2")),
            "trailing": 1 if int(d.get("trailing_active") or 0) == 1 else 0,
            "ai_score": _safe_float(feat.get("ai_score") if feat.get("ai_score") is not None else d.get("decision_confidence")),
            "expected_pnl_pct": _safe_float(feat.get("gate_expected_pnl_pct")),
            "funding": _safe_float(feat.get("funding")),
            "oi_delta": _safe_float(feat.get("oi_delta")),
            "etf_flow": _safe_float(feat.get("etf_flow")),
            "fear_greed": _safe_float(feat.get("fear_greed")),
            "macro_score": _safe_float(feat.get("macro_score")),
            "news_score": _safe_float(feat.get("news_score")),
            "market_score": _safe_float(feat.get("shock_score")),
            "volatility": _safe_float(feat.get("volatility")),
            "atr": _safe_float(feat.get("atr")),
            "volume": _safe_float(feat.get("volume")),
            "trend": _safe_float(feat.get("trend")),
            "vwap": None,
            "spread": _safe_float(feat.get("spread")),
            "timestamp": entry_ts,
            "hour": int(hour) if hour is not None else None,
            "weekday": int(weekday) if weekday is not None else None,
            "market_regime": feat.get("market_regime"),
            "created_at": closed_ts or entry_ts or int(time.time()),
            "_extra": {
                "source_db": "market_events.db",
                "source_table": table,
                "strategy": s_type,
                "entry_ts": entry_ts,
                "exit_ts": closed_ts,
                "mae": _safe_float(d.get("mae_pct") or feat.get("mae_pct")),
                "mfe": _safe_float(d.get("mfe_pct") or feat.get("mfe_pct")),
                "features": feat,
            },
        }
        yield snap


def _upsert_snapshot(research_conn: Any, snap: dict[str, Any]) -> str:
    """INSERT OR IGNORE — never overwrite existing (s40_signal_type, s40_signal_id).

    Returns: 'imported' | 'skipped' | 'error'
    """
    if snap.get("pnl_usd") is None:
        return "skipped"
    if not snap.get("s40_signal_id"):
        return "skipped"
    extra = snap.pop("_extra", {}) or {}
    payload = dict(snap)
    payload["snapshot_json"] = json.dumps({**snap, **extra}, default=str)

    try:
        cur = execute_with_retry(
            research_conn,
            f"""
            INSERT OR IGNORE INTO {_SNAP} (
              paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
              entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
              tp1, tp2, trailing, ai_score, expected_pnl_pct,
              funding, oi_delta, etf_flow, fear_greed, macro_score, news_score,
              market_score, volatility, atr, volume, trend, vwap, spread,
              timestamp, hour, weekday, market_regime, snapshot_json, created_at
            ) VALUES (
              ?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?,?,
              ?,?,?,?,?,?
            )
            """,
            (
                payload.get("paper_trade_id"),
                payload.get("s40_signal_type"),
                payload.get("s40_signal_id"),
                payload.get("symbol"),
                payload.get("direction"),
                payload.get("entry"),
                payload.get("exit_price"),
                payload.get("pnl_usd"),
                payload.get("pnl_pct"),
                payload.get("duration_sec"),
                payload.get("exit_reason"),
                payload.get("tp1"),
                payload.get("tp2"),
                int(payload.get("trailing") or 0),
                payload.get("ai_score"),
                payload.get("expected_pnl_pct"),
                payload.get("funding"),
                payload.get("oi_delta"),
                payload.get("etf_flow"),
                payload.get("fear_greed"),
                payload.get("macro_score"),
                payload.get("news_score"),
                payload.get("market_score"),
                payload.get("volatility"),
                payload.get("atr"),
                payload.get("volume"),
                payload.get("trend"),
                payload.get("vwap"),
                payload.get("spread"),
                payload.get("timestamp"),
                payload.get("hour"),
                payload.get("weekday"),
                payload.get("market_regime"),
                payload.get("snapshot_json"),
                int(payload.get("created_at") or time.time()),
            ),
        )
        rc = getattr(cur, "rowcount", None)
        if rc is None:
            # Fallback: assume imported if no error (some wrappers omit rowcount)
            return "imported"
        return "imported" if int(rc) > 0 else "skipped"
    except Exception as exc:
        logger.warning("backfill upsert failed: %s", exc)
        return "error"


def run_history_backfill(
    research_conn: Any,
    *,
    trades_db: Path | None = None,
    live_db: Path | None = None,
) -> dict[str, Any]:
    """Discover sources, UPSERT into research snapshots, verify count."""
    t0 = time.time()
    trades_db = trades_db or default_trades_db_path()
    live_db = live_db or default_live_db_path()

    sources = discover_trade_sources(trades_db=trades_db, live_db=live_db)
    found = sum(int(s.get("completed") or 0) for s in sources)

    imported = 0
    skipped = 0
    errors = 0
    by_source: list[dict[str, Any]] = []

    # trades.db sources
    if trades_db.exists() and trades_db.stat().st_size > 0:
        src_conn = sqlite3.connect(f"file:{trades_db}?mode=ro", uri=True)
        src_conn.row_factory = sqlite3.Row
        try:
            feat_idx = _load_trade_features_index(src_conn)
            for meta in sources:
                if meta.get("kind") != "trades_db":
                    continue
                src_def = next(s for s in TRADES_DB_SOURCES if s["table"] == meta["table"])
                n_imp = n_skip = n_err = 0
                for snap in _iter_trades_db_rows(src_conn, src_def, feat_idx):
                    result = _upsert_snapshot(research_conn, snap)
                    if result == "imported":
                        n_imp += 1
                        imported += 1
                    elif result == "skipped":
                        n_skip += 1
                        skipped += 1
                    else:
                        n_err += 1
                        errors += 1
                by_source.append({
                    "table": meta["table"],
                    "found": meta["completed"],
                    "imported": n_imp,
                    "skipped": n_skip,
                    "errors": n_err,
                })
        finally:
            src_conn.close()

    # live paper
    if live_db.exists() and live_db.stat().st_size > 0:
        live_conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
        live_conn.row_factory = sqlite3.Row
        try:
            meta = next((s for s in sources if s.get("kind") == "live_paper"), None)
            if meta and meta["completed"]:
                n_imp = n_skip = n_err = 0
                for snap in _iter_live_paper_rows(live_conn):
                    result = _upsert_snapshot(research_conn, snap)
                    if result == "imported":
                        n_imp += 1
                        imported += 1
                    elif result == "skipped":
                        n_skip += 1
                        skipped += 1
                    else:
                        n_err += 1
                        errors += 1
                by_source.append({
                    "table": meta["table"],
                    "found": meta["completed"],
                    "imported": n_imp,
                    "skipped": n_skip,
                    "errors": n_err,
                })
        finally:
            live_conn.close()

    try:
        research_conn.commit()
    except Exception:
        pass

    verify_count = 0
    try:
        verify_count = int(
            research_conn.execute(f"SELECT COUNT(*) AS n FROM {_SNAP}").fetchone()["n"] or 0
        )
    except Exception as exc:
        logger.warning("verify count failed: %s", exc)

    elapsed = round(time.time() - t0, 3)
    return {
        "ok": errors == 0,
        "trades_db": str(trades_db.resolve()),
        "live_db": str(live_db.resolve()),
        "sources": sources,
        "by_source": by_source,
        "trades_found": found,
        "trades_imported": imported,
        "trades_skipped": skipped,
        "errors": errors,
        "verify_count": verify_count,
        "elapsed_sec": elapsed,
    }


def format_history_backfill_report(result: dict[str, Any]) -> str:
    lines = [
        "History Backfill → research market_events_trade_snapshots_s56",
        f"  trades.db: {result.get('trades_db')}",
        f"  live.db:   {result.get('live_db')}",
        "",
        "Sources:",
    ]
    for s in result.get("sources") or []:
        lines.append(
            f"  · {s.get('table')}: completed={s.get('completed')} "
            f"({s.get('kind')})"
        )
    lines.extend([
        "",
        f"Trades found:    {result.get('trades_found')}",
        f"Trades imported: {result.get('trades_imported')}",
        f"Trades skipped:  {result.get('trades_skipped')}",
        f"Errors:          {result.get('errors')}",
        f"Elapsed time:    {result.get('elapsed_sec')}s",
        "",
        f"VERIFY: SELECT COUNT(*) FROM {_SNAP} => {result.get('verify_count')}",
    ])
    return "\n".join(lines)


__all__ = [
    "default_live_db_path",
    "default_trades_db_path",
    "discover_trade_sources",
    "format_history_backfill_report",
    "run_history_backfill",
]
