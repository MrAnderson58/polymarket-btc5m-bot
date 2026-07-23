"""S55.1 — Trade Intelligence Engine for S42 paper opens.

Gates new paper trades using similar historical closed trades + max-open cap.
Logs entry feature vectors and close outcomes into market_events_trade_features_s55.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_TABLE = "market_events_trade_features_s55"

# ===== S55 config (env-overridable) =====
S55_ENABLED = True
S55_MAX_OPEN_TRADES = 25
S55_SIMILAR_K = 100
S55_MIN_SIMILAR = 20
S55_MIN_EXPECTED_PNL_PCT = 0.0

# Numeric dims used for similarity (null-safe).
_NUMERIC_DIMS: tuple[tuple[str, float], ...] = (
    ("hour", 12.0),
    ("weekday", 3.5),
    ("volatility", 1.0),
    ("atr", 1.0),
    ("rsi", 50.0),
    ("funding", 0.05),
    ("oi_delta", 1.0),
    ("etf_flow", 1.0),
    ("macro_score", 1.0),
    ("news_score", 1.0),
    ("ai_score", 10.0),
    ("trend", 1.0),
    ("volume", 1.0),
    ("fear_greed", 50.0),
    ("btc_dominance", 10.0),
    ("spread", 0.01),
    ("funding_sign", 1.0),
    ("shock_score", 1.0),
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_s55_config_from_env() -> None:
    """Reload S55 flags from environment when vars are explicitly set."""
    global S55_ENABLED, S55_MAX_OPEN_TRADES, S55_SIMILAR_K, S55_MIN_SIMILAR, S55_MIN_EXPECTED_PNL_PCT
    if "S55_ENABLED" in os.environ:
        S55_ENABLED = _env_bool("S55_ENABLED", True)
    if "S55_MAX_OPEN_TRADES" in os.environ:
        try:
            S55_MAX_OPEN_TRADES = max(1, int(os.environ["S55_MAX_OPEN_TRADES"]))
        except (TypeError, ValueError):
            pass
    if "S55_SIMILAR_K" in os.environ:
        try:
            S55_SIMILAR_K = max(1, int(os.environ["S55_SIMILAR_K"]))
        except (TypeError, ValueError):
            pass
    if "S55_MIN_SIMILAR" in os.environ:
        try:
            S55_MIN_SIMILAR = max(0, int(os.environ["S55_MIN_SIMILAR"]))
        except (TypeError, ValueError):
            pass
    if "S55_MIN_EXPECTED_PNL_PCT" in os.environ:
        try:
            S55_MIN_EXPECTED_PNL_PCT = float(os.environ["S55_MIN_EXPECTED_PNL_PCT"])
        except (TypeError, ValueError):
            pass


def _apply_s55_defaults() -> None:
    global S55_ENABLED, S55_MAX_OPEN_TRADES, S55_SIMILAR_K, S55_MIN_SIMILAR, S55_MIN_EXPECTED_PNL_PCT
    S55_ENABLED = _env_bool("S55_ENABLED", True)
    try:
        S55_MAX_OPEN_TRADES = max(1, int(os.environ.get("S55_MAX_OPEN_TRADES", "25")))
    except (TypeError, ValueError):
        S55_MAX_OPEN_TRADES = 25
    try:
        S55_SIMILAR_K = max(1, int(os.environ.get("S55_SIMILAR_K", "100")))
    except (TypeError, ValueError):
        S55_SIMILAR_K = 100
    try:
        S55_MIN_SIMILAR = max(0, int(os.environ.get("S55_MIN_SIMILAR", "20")))
    except (TypeError, ValueError):
        S55_MIN_SIMILAR = 20
    try:
        S55_MIN_EXPECTED_PNL_PCT = float(os.environ.get("S55_MIN_EXPECTED_PNL_PCT", "0.0"))
    except (TypeError, ValueError):
        S55_MIN_EXPECTED_PNL_PCT = 0.0


_apply_s55_defaults()


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        keys = row.keys() if hasattr(row, "keys") else None
        if keys is not None and key not in keys:
            return default
        val = row[key]
        return default if val is None else val
    except Exception:
        return default


def build_entry_features(conn: Any, s40_row: Any) -> dict[str, Any]:
    """Build entry feature vector from an S40 signal row (+ optional enrichment)."""
    ts = int(_row_get(s40_row, "timestamp") or time.time())
    dt = datetime.fromtimestamp(ts)
    funding = _safe_float(_row_get(s40_row, "snapshot_funding"))
    oi = _safe_float(_row_get(s40_row, "snapshot_open_interest"))
    atr = _safe_float(_row_get(s40_row, "snapshot_atr"))
    volume = _safe_float(_row_get(s40_row, "snapshot_volume"))
    fear = _safe_float(_row_get(s40_row, "snapshot_fear_greed"))
    trend = _safe_float(_row_get(s40_row, "snapshot_trend"))
    news = _safe_float(_row_get(s40_row, "snapshot_news_score"))
    ai = _safe_float(_row_get(s40_row, "snapshot_decision_confidence"))

    # Best-effort extras from same-symbol recent snapshot if columns missing on row.
    rsi = None
    oi_delta = None
    etf_flow = None
    macro_score = None
    btc_dominance = None
    spread = None
    shock_score = None
    market_regime = None
    volatility = atr

    symbol = str(_row_get(s40_row, "symbol") or "").upper()
    try:
        snap = conn.execute(
            """
            SELECT funding, open_interest, atr, fear_greed, volume
            FROM market_snapshots_g3
            ORDER BY snapshot_ts DESC LIMIT 1
            """,
        ).fetchone()
        if snap:
            if funding is None:
                funding = _safe_float(snap["funding"])
            if oi is None:
                oi = _safe_float(snap["open_interest"])
            if atr is None:
                atr = _safe_float(snap["atr"])
                volatility = atr
            if fear is None:
                fear = _safe_float(snap["fear_greed"])
            if volume is None:
                volume = _safe_float(snap["volume"])
    except Exception:
        pass

    funding_sign = None
    if funding is not None:
        funding_sign = 1 if funding > 0 else (-1 if funding < 0 else 0)

    feats = {
        "symbol": symbol,
        "direction": str(_row_get(s40_row, "direction") or "").upper(),
        "hour": dt.hour,
        "weekday": dt.weekday(),
        "volatility": volatility,
        "atr": atr,
        "rsi": rsi,
        "funding": funding,
        "oi_delta": oi_delta if oi_delta is not None else oi,
        "etf_flow": etf_flow,
        "macro_score": macro_score,
        "news_score": news,
        "ai_score": ai,
        "trend": trend,
        "volume": volume,
        "fear_greed": fear,
        "btc_dominance": btc_dominance,
        "spread": spread,
        "funding_sign": funding_sign,
        "market_regime": market_regime,
        "shock_score": shock_score,
    }
    feats["features_json"] = json.dumps({k: v for k, v in feats.items() if k != "features_json"}, default=str)
    return feats


def _similarity_score(cur: dict[str, Any], hist: dict[str, Any]) -> float:
    """Weighted similarity in [0, 1]; hard-filters direction elsewhere."""
    score = 0.0
    weight_sum = 0.0
    for key, scale in _NUMERIC_DIMS:
        a = _safe_float(cur.get(key))
        b = _safe_float(hist.get(key))
        if a is None or b is None:
            continue
        w = 1.0
        weight_sum += w
        diff = abs(a - b) / max(scale, 1e-9)
        score += max(0.0, 1.0 - min(diff, 1.0)) * w

    # Soft boost for same symbol
    if cur.get("symbol") and hist.get("symbol") and str(cur["symbol"]) == str(hist["symbol"]):
        score += 0.15
        weight_sum += 0.15

    if weight_sum <= 0:
        return 0.0
    return round(min(1.0, score / weight_sum), 4)


def find_similar_trades(
    conn: Any,
    features: dict[str, Any],
    *,
    k: int | None = None,
    pool_limit: int = 800,
) -> list[dict[str, Any]]:
    """Top-K similar *closed* feature rows with same direction."""
    k = S55_SIMILAR_K if k is None else int(k)
    direction = str(features.get("direction") or "").upper()
    if not direction:
        return []
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM {_TABLE}
            WHERE closed_at IS NOT NULL
              AND direction = ?
              AND result IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (direction, pool_limit),
        ).fetchall()
    except Exception as exc:
        logger.warning("s55 find_similar_trades failed: %s", exc)
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        hist = dict(r)
        sim = _similarity_score(features, hist)
        if sim <= 0:
            continue
        hist["similarity"] = sim
        scored.append((sim, hist))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:k]]


def estimate_from_neighbors(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate expected metrics from similar closed trades."""
    empty = {
        "winrate": 0.0,
        "expected_pnl_pct": 0.0,
        "p_tp1": 0.0,
        "p_sl": 0.0,
        "p_trailing": 0.0,
        "avg_mfe": 0.0,
        "avg_mae": 0.0,
        "n": 0,
    }
    if not rows:
        return empty
    n = len(rows)
    wins = sum(1 for r in rows if str(r.get("result") or "").upper() == "WIN")
    pnls = [_safe_float(r.get("pnl_pct")) or 0.0 for r in rows]
    mfes = [_safe_float(r.get("mfe_pct")) or 0.0 for r in rows]
    maes = [_safe_float(r.get("mae_pct")) or 0.0 for r in rows]
    tp1 = sum(1 for r in rows if int(r.get("reached_tp1") or 0) == 1)
    sl = sum(1 for r in rows if int(r.get("stopped") or 0) == 1)
    trail = sum(1 for r in rows if int(r.get("trailing") or 0) == 1)
    return {
        "winrate": round(100.0 * wins / n, 1),
        "expected_pnl_pct": round(sum(pnls) / n, 4),
        "p_tp1": round(100.0 * tp1 / n, 1),
        "p_sl": round(100.0 * sl / n, 1),
        "p_trailing": round(100.0 * trail / n, 1),
        "avg_mfe": round(sum(mfes) / n, 4),
        "avg_mae": round(sum(maes) / n, 4),
        "n": n,
    }


def should_open_trade(
    conn: Any,
    *,
    features: dict[str, Any],
    open_count: int,
) -> tuple[bool, str, dict[str, Any]]:
    """
    Gate decision.
    Returns (allow, gate_decision, estimate).
    """
    refresh_s55_config_from_env()
    estimate = estimate_from_neighbors([])
    if not S55_ENABLED:
        return True, "disabled", estimate

    if open_count >= S55_MAX_OPEN_TRADES:
        return False, "max_open", estimate

    neighbors = find_similar_trades(conn, features, k=S55_SIMILAR_K)
    estimate = estimate_from_neighbors(neighbors)
    estimate["similar_count"] = len(neighbors)

    if len(neighbors) < S55_MIN_SIMILAR:
        return True, "cold_start", estimate

    if float(estimate["expected_pnl_pct"]) < float(S55_MIN_EXPECTED_PNL_PCT):
        return False, "reject_expected_pnl", estimate

    return True, "open", estimate


def record_trade_features_on_open(
    conn: Any,
    *,
    paper_trade_id: int | None,
    s40_signal_type: str,
    s40_signal_id: int,
    features: dict[str, Any],
    gate_decision: str,
    estimate: dict[str, Any],
    now: int | None = None,
) -> None:
    """Persist entry features + gate metadata (outcomes filled later on close)."""
    now = int(now if now is not None else time.time())
    try:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_TABLE} (
              paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
              hour, weekday, volatility, atr, rsi, funding, oi_delta, etf_flow,
              macro_score, news_score, ai_score, trend, volume, fear_greed,
              btc_dominance, spread, funding_sign, market_regime, shock_score,
              features_json, gate_decision, gate_expected_pnl_pct, similar_count,
              created_at
            ) VALUES (
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?,
              ?
            )
            """,
            (
                paper_trade_id,
                str(s40_signal_type),
                int(s40_signal_id),
                str(features.get("symbol") or ""),
                str(features.get("direction") or ""),
                features.get("hour"),
                features.get("weekday"),
                features.get("volatility"),
                features.get("atr"),
                features.get("rsi"),
                features.get("funding"),
                features.get("oi_delta"),
                features.get("etf_flow"),
                features.get("macro_score"),
                features.get("news_score"),
                features.get("ai_score"),
                features.get("trend"),
                features.get("volume"),
                features.get("fear_greed"),
                features.get("btc_dominance"),
                features.get("spread"),
                features.get("funding_sign"),
                features.get("market_regime"),
                features.get("shock_score"),
                features.get("features_json"),
                gate_decision,
                float(estimate.get("expected_pnl_pct") or 0.0),
                int(estimate.get("similar_count") or estimate.get("n") or 0),
                now,
            ),
        )
    except Exception as exc:
        logger.warning("s55 record_trade_features_on_open failed: %s", exc)


def outcome_flags_from_exit(exit_reason: str | None) -> dict[str, int]:
    reason = str(exit_reason or "").upper()
    return {
        "reached_tp1": 1 if reason in ("TP1", "TP2", "TRAILING") else 0,
        "reached_tp2": 1 if reason == "TP2" else 0,
        "stopped": 1 if reason in ("STOP", "SL") else 0,
        "trailing": 1 if reason == "TRAILING" else 0,
    }


def finalize_trade_features_on_close(
    conn: Any,
    *,
    paper_trade_id: int,
    s40_signal_type: str,
    s40_signal_id: int,
    result: str | None,
    pnl_pct: float | None,
    pnl_usd: float | None,
    mae_pct: float | None,
    mfe_pct: float | None,
    exit_reason: str | None,
    duration_sec: int | None,
    now: int | None = None,
) -> None:
    """Attach close outcomes to the feature row (create stub if missing)."""
    now = int(now if now is not None else time.time())
    flags = outcome_flags_from_exit(exit_reason)
    try:
        existing = conn.execute(
            f"""
            SELECT id FROM {_TABLE}
            WHERE s40_signal_type = ? AND s40_signal_id = ?
            """,
            (str(s40_signal_type), int(s40_signal_id)),
        ).fetchone()
        if existing:
            conn.execute(
                f"""
                UPDATE {_TABLE} SET
                  paper_trade_id = COALESCE(?, paper_trade_id),
                  result = ?, pnl_pct = ?, pnl_usd = ?,
                  mae_pct = ?, mfe_pct = ?,
                  reached_tp1 = ?, reached_tp2 = ?, stopped = ?, trailing = ?,
                  duration_sec = ?, exit_reason = ?, closed_at = ?
                WHERE s40_signal_type = ? AND s40_signal_id = ?
                """,
                (
                    paper_trade_id,
                    result,
                    pnl_pct,
                    pnl_usd,
                    mae_pct,
                    mfe_pct,
                    flags["reached_tp1"],
                    flags["reached_tp2"],
                    flags["stopped"],
                    flags["trailing"],
                    duration_sec,
                    exit_reason,
                    now,
                    str(s40_signal_type),
                    int(s40_signal_id),
                ),
            )
        else:
            # Closed without open-time feature row (legacy / disabled path).
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {_TABLE} (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  result, pnl_pct, pnl_usd, mae_pct, mfe_pct,
                  reached_tp1, reached_tp2, stopped, trailing,
                  duration_sec, exit_reason, gate_decision, created_at, closed_at
                ) VALUES (?, ?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy', ?, ?)
                """,
                (
                    paper_trade_id,
                    str(s40_signal_type),
                    int(s40_signal_id),
                    result,
                    pnl_pct,
                    pnl_usd,
                    mae_pct,
                    mfe_pct,
                    flags["reached_tp1"],
                    flags["reached_tp2"],
                    flags["stopped"],
                    flags["trailing"],
                    duration_sec,
                    exit_reason,
                    now,
                    now,
                ),
            )
    except Exception as exc:
        logger.warning("s55 finalize_trade_features_on_close failed: %s", exc)


def gate_stats_today(conn: Any, *, day_start: int | None = None) -> dict[str, Any]:
    """Counts for report / ops."""
    refresh_s55_config_from_env()
    if day_start is None:
        dt = datetime.fromtimestamp(time.time())
        day_start = int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    out = {
        "enabled": S55_ENABLED,
        "max_open": S55_MAX_OPEN_TRADES,
        "min_expected_pnl_pct": S55_MIN_EXPECTED_PNL_PCT,
        "accepted": 0,
        "rejected": 0,
        "cold_start": 0,
        "avg_expected_pnl_accepted": 0.0,
    }
    try:
        rows = conn.execute(
            f"""
            SELECT gate_decision, gate_expected_pnl_pct
            FROM {_TABLE}
            WHERE created_at >= ?
            """,
            (day_start,),
        ).fetchall()
    except Exception:
        return out
    accepted_pnls: list[float] = []
    for r in rows:
        d = str(r["gate_decision"] or "")
        if d in ("open", "cold_start", "disabled"):
            out["accepted"] += 1
            if d == "cold_start":
                out["cold_start"] += 1
            if r["gate_expected_pnl_pct"] is not None and d in ("open", "cold_start"):
                accepted_pnls.append(float(r["gate_expected_pnl_pct"]))
        elif d.startswith("reject") or d == "max_open":
            out["rejected"] += 1
    if accepted_pnls:
        out["avg_expected_pnl_accepted"] = round(sum(accepted_pnls) / len(accepted_pnls), 4)
    return out


def format_s55_gate_block(conn: Any, *, open_count: int = 0) -> list[str]:
    stats = gate_stats_today(conn)
    return [
        "",
        "S55 Gate",
        f"  enabled={stats['enabled']}  max_open={stats['max_open']}  current_open={open_count}",
        f"  min_expected_pnl_pct={stats['min_expected_pnl_pct']}",
        f"  accepted_today={stats['accepted']}  rejected_today={stats['rejected']}  "
        f"cold_start={stats['cold_start']}",
        f"  avg_expected_pnl_accepted={stats['avg_expected_pnl_accepted']:+.4f}%",
    ]


__all__ = [
    "S55_ENABLED",
    "S55_MAX_OPEN_TRADES",
    "S55_MIN_EXPECTED_PNL_PCT",
    "S55_MIN_SIMILAR",
    "S55_SIMILAR_K",
    "build_entry_features",
    "estimate_from_neighbors",
    "finalize_trade_features_on_close",
    "find_similar_trades",
    "format_s55_gate_block",
    "gate_stats_today",
    "outcome_flags_from_exit",
    "record_trade_features_on_open",
    "refresh_s55_config_from_env",
    "should_open_trade",
]
