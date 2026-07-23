"""S56 — Trade Intelligence & LLM Post-Mortem (observe / suggest only).

Never auto-applies strategy changes. Suggestions stay WAITING_APPROVAL until a human
approves; approval only records status + Cursor task text (e.g. S56.1).

S56.1 — lock-safe writes (execute_with_retry), backfill from closed S42 trades.
S56.2 — deep statistical analysis (symbols / hours / direction / exits / features).
         Suggestions off by default until analysis is rich enough.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from bot.research.market_events.db import execute_with_retry, is_database_locked, retry_on_db_locked

logger = logging.getLogger(__name__)

_SNAP = "market_events_trade_snapshots_s56"
_RUNS = "market_events_postmortem_runs_s56"
_SUGGEST = "market_events_rule_suggestions_s56"
_OPS = "market_events_postmortem_ops_s56"
_TRADES = "market_events_paper_trades_s42"
_FEATURES = "market_events_trade_features_s55"

STATUS_WAITING = "WAITING_APPROVAL"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_APPLIED = "APPLIED"

S56_TOP_N = 500
S56_RCA_EVERY_N = 1000
S56_LLM_ENABLED = False
S56_MIN_EVIDENCE = 30
# S56.2: do not invent weak rule proposals while feature coverage is thin.
S56_SUGGESTIONS_ENABLED = False
S56_SYMBOL_TOP_N = 20

_NUMERIC_FEATURES = (
    "ai_score",
    "expected_pnl_pct",
    "funding",
    "oi_delta",
    "etf_flow",
    "fear_greed",
    "macro_score",
    "news_score",
    "market_score",
    "volatility",
    "atr",
    "volume",
    "trend",
    "vwap",
    "spread",
    "hour",
    "weekday",
    "duration_sec",
)

_CATEGORICAL_FEATURES = (
    "symbol",
    "direction",
    "exit_reason",
    "market_regime",
)

_EXIT_CANON = {
    "STOP": "STOP",
    "STOP_LOSS": "STOP",
    "TP1": "TP1",
    "TAKE_PROFIT_1": "TP1",
    "TP2": "TP2",
    "TAKE_PROFIT_2": "TP2",
    "TRAILING": "Trailing",
    "TRAIL": "Trailing",
    "TIMEOUT": "Timeout",
    "STALE_TIMEOUT": "Timeout",
    "PORTFOLIO_REPLACE": "Portfolio Replace",
}

_EXIT_ORDER = ("STOP", "TP1", "TP2", "Trailing", "Timeout", "Portfolio Replace", "Other")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_s56_config_from_env() -> None:
    global S56_TOP_N, S56_RCA_EVERY_N, S56_LLM_ENABLED, S56_MIN_EVIDENCE
    global S56_SUGGESTIONS_ENABLED, S56_SYMBOL_TOP_N
    if "S56_TOP_N" in os.environ:
        try:
            S56_TOP_N = max(10, int(os.environ["S56_TOP_N"]))
        except (TypeError, ValueError):
            pass
    if "S56_RCA_EVERY_N" in os.environ:
        try:
            S56_RCA_EVERY_N = max(50, int(os.environ["S56_RCA_EVERY_N"]))
        except (TypeError, ValueError):
            pass
    if "S56_LLM_ENABLED" in os.environ:
        S56_LLM_ENABLED = _env_bool("S56_LLM_ENABLED", False)
    if "S56_MIN_EVIDENCE" in os.environ:
        try:
            S56_MIN_EVIDENCE = max(5, int(os.environ["S56_MIN_EVIDENCE"]))
        except (TypeError, ValueError):
            pass
    if "S56_SUGGESTIONS_ENABLED" in os.environ:
        S56_SUGGESTIONS_ENABLED = _env_bool("S56_SUGGESTIONS_ENABLED", False)
    if "S56_SYMBOL_TOP_N" in os.environ:
        try:
            S56_SYMBOL_TOP_N = max(5, int(os.environ["S56_SYMBOL_TOP_N"]))
        except (TypeError, ValueError):
            pass


def _apply_defaults() -> None:
    global S56_TOP_N, S56_RCA_EVERY_N, S56_LLM_ENABLED, S56_MIN_EVIDENCE
    global S56_SUGGESTIONS_ENABLED, S56_SYMBOL_TOP_N
    try:
        S56_TOP_N = max(10, int(os.environ.get("S56_TOP_N", "500")))
    except (TypeError, ValueError):
        S56_TOP_N = 500
    try:
        S56_RCA_EVERY_N = max(50, int(os.environ.get("S56_RCA_EVERY_N", "1000")))
    except (TypeError, ValueError):
        S56_RCA_EVERY_N = 1000
    S56_LLM_ENABLED = _env_bool("S56_LLM_ENABLED", False)
    try:
        S56_MIN_EVIDENCE = max(5, int(os.environ.get("S56_MIN_EVIDENCE", "30")))
    except (TypeError, ValueError):
        S56_MIN_EVIDENCE = 30
    S56_SUGGESTIONS_ENABLED = _env_bool("S56_SUGGESTIONS_ENABLED", False)
    try:
        S56_SYMBOL_TOP_N = max(5, int(os.environ.get("S56_SYMBOL_TOP_N", "20")))
    except (TypeError, ValueError):
        S56_SYMBOL_TOP_N = 20


_apply_defaults()


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _row(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _ops_get(conn: Any, key: str, default: str = "0") -> str:
    try:
        r = conn.execute(f"SELECT value FROM {_OPS} WHERE key = ?", (key,)).fetchone()
        return str(r["value"]) if r else default
    except Exception:
        return default


def _ops_set(conn: Any, key: str, value: str, now: int | None = None) -> None:
    now = int(now if now is not None else time.time())
    execute_with_retry(
        conn,
        f"""
        INSERT OR REPLACE INTO {_OPS} (key, value, updated_at)
        VALUES (?, ?, ?)
        """,
        (key, value, now),
    )


def closed_trade_count(conn: Any) -> int:
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE status = 'CLOSED'",
            ).fetchone()["n"]
            or 0
        )
    except Exception:
        return 0


def snapshot_count(conn: Any) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {_SNAP}").fetchone()["n"] or 0)
    except Exception:
        return 0


def diagnose_snapshots(conn: Any, *, live_conn: Any | None = None) -> dict[str, Any]:
    """Explain snapshots=0 with numbers (S56.1 Block 2).

    S60: ``live_conn`` supplies closed S42 counts; ``conn`` is research snapshots DB.
    """
    src = live_conn or conn
    closed_n = closed_trade_count(src)
    snap_n = snapshot_count(conn)
    missing = max(0, closed_n - snap_n)
    try:
        conn.execute(f"SELECT 1 FROM {_SNAP} LIMIT 1")
    except Exception as exc:
        return {
            "table_ok": False,
            "error": str(exc),
            "closed_s42": closed_n,
            "snapshots": 0,
            "missing": closed_n,
            "reasons": [f"table missing/unreadable: {exc}"],
            "fix": "run market-research-migrate then trade-postmortem --backfill",
        }

    reasons: list[str] = []
    if snap_n == 0 and closed_n == 0:
        reasons.append("no CLOSED rows in market_events_paper_trades_s42 on live DB")
    if snap_n == 0 and closed_n > 0:
        reasons.append(
            f"{closed_n} closed S42 trades exist but 0 research snapshots — "
            "run trade-postmortem --backfill"
        )
    if missing > 0 and snap_n > 0:
        reasons.append(f"{missing} closed trades still lack snapshots (partial backfill)")
    if not reasons and snap_n > 0:
        reasons.append("snapshots healthy")

    return {
        "table_ok": True,
        "closed_s42": closed_n,
        "snapshots": snap_n,
        "missing": missing,
        "reasons": reasons,
        "fix": (
            f"python -m bot.research.market_events trade-postmortem --backfill-last {min(closed_n, 5000) or 5000}"
            if missing
            else "ok"
        ),
    }


def record_close_snapshot(
    conn: Any,
    *,
    trade_row: Any,
    now: int | None = None,
    trigger_postmortem: bool = True,
    features: dict[str, Any] | None = None,
) -> bool:
    """Persist full close snapshot. Returns True if write succeeded.

    ``features`` may be preloaded from the live DB when ``conn`` is research storage.
    """
    now = int(now if now is not None else time.time())
    t = _row(trade_row)
    paper_id = int(t.get("id") or 0)
    s_type = str(t.get("s40_signal_type") or "")
    s_id = int(t.get("s40_signal_id") or 0)

    feat: dict[str, Any] = dict(features or {})
    if not feat:
        try:
            fr = conn.execute(
                f"""
                SELECT * FROM {_FEATURES}
                WHERE s40_signal_type = ? AND s40_signal_id = ?
                """,
                (s_type, s_id),
            ).fetchone()
            if fr:
                feat = _row(fr)
        except Exception:
            pass

    ts = int(t.get("created_at") or now)
    dt = datetime.fromtimestamp(ts)
    hour = feat.get("hour")
    weekday = feat.get("weekday")
    if hour is None:
        hour = dt.hour
    if weekday is None:
        weekday = dt.weekday()

    trailing = 1 if (
        int(t.get("trailing_active") or 0) == 1
        or str(t.get("exit_reason") or "").upper() == "TRAILING"
        or int(feat.get("trailing") or 0) == 1
    ) else 0

    snap = {
        "paper_trade_id": paper_id or None,
        "s40_signal_type": s_type,
        "s40_signal_id": s_id,
        "symbol": str(t.get("symbol") or feat.get("symbol") or ""),
        "direction": str(t.get("direction") or feat.get("direction") or "").upper(),
        "entry": _safe_float(t.get("entry")),
        "exit_price": _safe_float(t.get("exit_price")),
        "pnl_usd": _safe_float(t.get("pnl_usd")),
        "pnl_pct": _safe_float(t.get("pnl_pct")),
        "duration_sec": int(t.get("holding_seconds") or feat.get("duration_sec") or 0) or None,
        "exit_reason": t.get("exit_reason") or feat.get("exit_reason"),
        "tp1": _safe_float(t.get("tp1")),
        "tp2": _safe_float(t.get("tp2")),
        "trailing": trailing,
        "ai_score": _safe_float(feat.get("ai_score") if feat.get("ai_score") is not None else t.get("decision_confidence")),
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
        "timestamp": ts,
        "hour": int(hour) if hour is not None else None,
        "weekday": int(weekday) if weekday is not None else None,
        "market_regime": feat.get("market_regime"),
    }
    snap["snapshot_json"] = json.dumps(snap, default=str)

    try:
        execute_with_retry(
            conn,
            f"""
            INSERT OR REPLACE INTO {_SNAP} (
              paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
              entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
              tp1, tp2, trailing, ai_score, expected_pnl_pct,
              funding, oi_delta, etf_flow, fear_greed, macro_score, news_score,
              market_score, volatility, atr, volume, trend, vwap, spread,
              timestamp, hour, weekday, market_regime, snapshot_json, created_at
            ) VALUES (
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?
            )
            """,
            (
                snap["paper_trade_id"], snap["s40_signal_type"], snap["s40_signal_id"],
                snap["symbol"], snap["direction"],
                snap["entry"], snap["exit_price"], snap["pnl_usd"], snap["pnl_pct"],
                snap["duration_sec"], snap["exit_reason"],
                snap["tp1"], snap["tp2"], snap["trailing"], snap["ai_score"],
                snap["expected_pnl_pct"],
                snap["funding"], snap["oi_delta"], snap["etf_flow"], snap["fear_greed"],
                snap["macro_score"], snap["news_score"],
                snap["market_score"], snap["volatility"], snap["atr"], snap["volume"],
                snap["trend"], snap["vwap"], snap["spread"],
                snap["timestamp"], snap["hour"], snap["weekday"], snap["market_regime"],
                snap["snapshot_json"], now,
            ),
        )
    except Exception as exc:
        if is_database_locked(exc):
            logger.warning("s56 record_close_snapshot locked after retries: %s", exc)
        else:
            logger.warning("s56 record_close_snapshot failed: %s", exc)
        return False

    if trigger_postmortem:
        try:
            maybe_run_postmortem(conn, now=now)
        except Exception as exc:
            logger.warning("s56 maybe_run_postmortem failed: %s", exc)
    return True


def backfill_snapshots(
    conn: Any,
    *,
    limit: int | None = None,
    batch_size: int = 500,
    run_rca: bool = True,
    now: int | None = None,
    live_conn: Any | None = None,
) -> dict[str, Any]:
    """Build snapshots from already-closed S42 trades. Then optionally run postmortem once.

    S60: read closed trades from ``live_conn`` (default ``conn``), write snapshots to ``conn`` (research).
    """
    now = int(now if now is not None else time.time())
    refresh_s56_config_from_env()
    src = live_conn or conn

    sql = f"""
        SELECT t.*
        FROM {_TRADES} t
        WHERE t.status = 'CLOSED'
          AND t.pnl_usd IS NOT NULL
        ORDER BY t.closed_at DESC
    """
    params: tuple = ()
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params = (int(limit),)

    try:
        rows = src.execute(sql, params).fetchall()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "written": 0, "failed": 0}

    # Skip trades that already have research snapshots
    existing: set[tuple[str, int]] = set()
    try:
        for r in conn.execute(
            f"SELECT s40_signal_type, s40_signal_id FROM {_SNAP}",
        ).fetchall():
            existing.add((str(r["s40_signal_type"]), int(r["s40_signal_id"])))
    except Exception:
        existing = set()

    written = 0
    failed = 0
    skipped = 0
    for i, r in enumerate(rows):
        key = (str(r["s40_signal_type"]), int(r["s40_signal_id"]))
        if key in existing:
            skipped += 1
            continue
        feat = None
        try:
            fr = src.execute(
                f"""
                SELECT * FROM {_FEATURES}
                WHERE s40_signal_type = ? AND s40_signal_id = ?
                """,
                key,
            ).fetchone()
            if fr:
                feat = dict(fr)
        except Exception:
            feat = None
        ok = record_close_snapshot(
            conn, trade_row=r, now=now, trigger_postmortem=False, features=feat,
        )
        if ok:
            written += 1
            existing.add(key)
        else:
            failed += 1
        if batch_size > 0 and (i + 1) % batch_size == 0:
            try:
                retry_on_db_locked(conn.commit)
            except Exception as exc:
                logger.warning("s56 backfill mid-commit failed: %s", exc)

    postmortem = None
    if run_rca and written > 0:
        try:
            postmortem = run_postmortem(conn, force=True, now=now)
        except Exception as exc:
            logger.warning("s56 backfill postmortem failed: %s", exc)
            postmortem = {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "candidates": len(rows),
        "written": written,
        "failed": failed,
        "skipped": skipped,
        "snapshots_total": snapshot_count(conn),
        "postmortem": postmortem,
    }


def top_winners(conn: Any, *, n: int | None = None) -> list[dict[str, Any]]:
    refresh_s56_config_from_env()
    n = int(n if n is not None else S56_TOP_N)
    rows = conn.execute(
        f"""
        SELECT * FROM {_SNAP}
        WHERE pnl_usd IS NOT NULL AND pnl_usd > 0
        ORDER BY pnl_usd DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    return [_row(r) for r in rows]


def top_losers(conn: Any, *, n: int | None = None) -> list[dict[str, Any]]:
    refresh_s56_config_from_env()
    n = int(n if n is not None else S56_TOP_N)
    rows = conn.execute(
        f"""
        SELECT * FROM {_SNAP}
        WHERE pnl_usd IS NOT NULL AND pnl_usd < 0
        ORDER BY pnl_usd ASC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    return [_row(r) for r in rows]


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((x - m) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5


def _stars_from_effect(effect: float) -> str:
    if effect >= 0.8:
        return "+++++"
    if effect >= 0.5:
        return "++++"
    if effect >= 0.35:
        return "+++"
    if effect >= 0.2:
        return "++"
    if effect >= 0.1:
        return "+"
    return ""


def _canon_exit(reason: Any) -> str:
    key = str(reason or "").strip().upper()
    if not key:
        return "Other"
    return _EXIT_CANON.get(key, "Other")


def bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """PnL / winrate / expectancy / PF / avg win / avg loss for a bucket."""
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "pnl": 0.0,
            "winrate": None,
            "expectancy": None,
            "pf": None,
            "avg_win": None,
            "avg_loss": None,
            "wins": 0,
            "losses": 0,
        }
    pnls = [float(r.get("pnl_usd") or 0.0) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 1e-12:
        pf: float | None = gross_win / gross_loss
    elif gross_win > 0:
        pf = float("inf")
    else:
        pf = 0.0
    return {
        "n": n,
        "pnl": round(sum(pnls), 4),
        "winrate": round(100.0 * len(wins) / n, 2),
        "expectancy": round(sum(pnls) / n, 4),
        "pf": (round(pf, 4) if pf != float("inf") else None) if pf is not None else None,
        "pf_inf": pf == float("inf"),
        "avg_win": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else None,
        "wins": len(wins),
        "losses": len(losses),
    }


def load_all_snapshots(conn: Any) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            f"SELECT * FROM {_SNAP} WHERE pnl_usd IS NOT NULL",
        ).fetchall()
    except Exception:
        return []
    return [_row(r) for r in rows]


def compute_deep_stats(
    conn: Any,
    *,
    symbol_top_n: int | None = None,
) -> dict[str, Any]:
    """S56.2 — pattern tables over all snapshots (not idea generation)."""
    refresh_s56_config_from_env()
    top_n = int(symbol_top_n if symbol_top_n is not None else S56_SYMBOL_TOP_N)
    rows = load_all_snapshots(conn)
    overall = bucket_metrics(rows)

    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_sym_dir: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_exit: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        sym = str(r.get("symbol") or "?").upper()
        direction = str(r.get("direction") or "?").upper()
        by_symbol[sym].append(r)
        by_sym_dir[(sym, direction)].append(r)
        by_exit[_canon_exit(r.get("exit_reason"))].append(r)
        h = r.get("hour")
        try:
            hi = int(h) if h is not None else -1
        except (TypeError, ValueError):
            hi = -1
        if 0 <= hi <= 23:
            by_hour[hi].append(r)

    # TOP-N symbols by trade count, then sorted by PnL ascending (worst first).
    ranked = sorted(by_symbol.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top_n]
    symbols: list[dict[str, Any]] = []
    for sym, bucket in ranked:
        m = bucket_metrics(bucket)
        symbols.append({"symbol": sym, **m})
    symbols.sort(key=lambda x: (x["pnl"], -x["n"]))

    hours: list[dict[str, Any]] = []
    for h in range(24):
        m = bucket_metrics(by_hour.get(h, []))
        hours.append({"hour": h, **m})

    symbol_direction: list[dict[str, Any]] = []
    for (sym, direction), bucket in sorted(by_sym_dir.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        m = bucket_metrics(bucket)
        symbol_direction.append({"symbol": sym, "direction": direction, **m})
    symbol_direction.sort(key=lambda x: (x["pnl"], -x["n"]))

    exits: list[dict[str, Any]] = []
    for label in _EXIT_ORDER:
        m = bucket_metrics(by_exit.get(label, []))
        if m["n"] == 0 and label == "Other":
            continue
        exits.append({"exit_reason": label, **m})
    for label, bucket in by_exit.items():
        if label not in _EXIT_ORDER:
            exits.append({"exit_reason": label, **bucket_metrics(bucket)})

    coverage = {
        "snapshots": len(rows),
        "symbols": len(by_symbol),
        "with_funding": sum(1 for r in rows if r.get("funding") is not None),
        "with_news_score": sum(1 for r in rows if r.get("news_score") is not None),
        "with_macro_score": sum(1 for r in rows if r.get("macro_score") is not None),
        "with_market_regime": sum(1 for r in rows if r.get("market_regime") is not None),
        "with_ai_score": sum(1 for r in rows if r.get("ai_score") is not None),
        "with_expected_pnl": sum(1 for r in rows if r.get("expected_pnl_pct") is not None),
        "with_duration": sum(1 for r in rows if r.get("duration_sec") is not None),
    }

    return {
        "overall": overall,
        "symbols_top": symbols,
        "hours": hours,
        "symbol_direction": symbol_direction,
        "exits": exits,
        "coverage": coverage,
    }


def _categorical_importance(
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
    feat: str,
) -> dict[str, Any] | None:
    """Winrate-gap effect across categories (null-safe)."""
    def _vals(rows: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for r in rows:
            v = r.get(feat)
            if v is None or str(v).strip() == "":
                continue
            if feat == "exit_reason":
                out.append(_canon_exit(v))
            else:
                out.append(str(v).strip().upper() if feat in ("symbol", "direction") else str(v))
        return out

    wv, lv = _vals(winners), _vals(losers)
    if len(wv) < 5 or len(lv) < 5:
        return None
    cats = sorted(set(wv) | set(lv))
    if len(cats) < 2:
        return None
    # Max |P(cat|win) - P(cat|lose)| as effect proxy.
    best_gap = 0.0
    best_cat = cats[0]
    for c in cats:
        pw = sum(1 for x in wv if x == c) / len(wv)
        pl = sum(1 for x in lv if x == c) / len(lv)
        gap = abs(pw - pl)
        if gap > best_gap:
            best_gap = gap
            best_cat = c
    return {
        "feature": feat,
        "kind": "categorical",
        "top_category": best_cat,
        "winners_avg": None,
        "losers_avg": None,
        "delta": round(best_gap, 4),
        "effect_size": round(best_gap, 4),
        "stars": _stars_from_effect(best_gap),
        "n_winners": len(wv),
        "n_losers": len(lv),
        "n_categories": len(cats),
    }


def compute_feature_importance(
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Effect-size ranking over numeric + categorical dims present in data."""
    out: list[dict[str, Any]] = []
    for feat in _NUMERIC_FEATURES:
        wv = [_safe_float(r.get(feat)) for r in winners]
        lv = [_safe_float(r.get(feat)) for r in losers]
        wv = [v for v in wv if v is not None]
        lv = [v for v in lv if v is not None]
        if len(wv) < 5 or len(lv) < 5:
            continue
        mw, ml = _mean(wv), _mean(lv)
        assert mw is not None and ml is not None
        pooled = ((_std(wv) ** 2 + _std(lv) ** 2) / 2) ** 0.5
        effect = abs(mw - ml) / pooled if pooled > 1e-12 else abs(mw - ml)
        out.append({
            "feature": feat,
            "kind": "numeric",
            "winners_avg": round(mw, 4),
            "losers_avg": round(ml, 4),
            "delta": round(mw - ml, 4),
            "effect_size": round(effect, 4),
            "stars": _stars_from_effect(effect),
            "n_winners": len(wv),
            "n_losers": len(lv),
        })
    for feat in _CATEGORICAL_FEATURES:
        item = _categorical_importance(winners, losers, feat)
        if item:
            out.append(item)
    out.sort(key=lambda x: x["effect_size"], reverse=True)
    return out


def _rate(rows: list[dict[str, Any]], pred) -> tuple[float, int]:
    if not rows:
        return 0.0, 0
    hit = sum(1 for r in rows if pred(r))
    return 100.0 * hit / len(rows), hit


def compute_rca(
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Automatic contrasts — numbers only."""
    findings: list[dict[str, Any]] = []

    def add(text: str, *, among: str, pct: float, n: int, base: int) -> None:
        if base < S56_MIN_EVIDENCE or pct < 55.0:
            return
        findings.append({
            "statement": text,
            "among": among,
            "pct": round(pct, 1),
            "n": n,
            "base": base,
        })

    # LONG + negative funding among losers
    long_losers = [r for r in losers if str(r.get("direction")) == "LONG"]
    if long_losers:
        pct, n = _rate(long_losers, lambda r: (_safe_float(r.get("funding")) or 0) < 0)
        add(
            f"{pct:.0f}% убыточных LONG были при отрицательном Funding",
            among="losers_LONG",
            pct=pct,
            n=n,
            base=len(long_losers),
        )

    short_winners = [r for r in winners if str(r.get("direction")) == "SHORT"]
    if short_winners:
        pct, n = _rate(short_winners, lambda r: (_safe_float(r.get("etf_flow")) or 0) > 0)
        add(
            f"{pct:.0f}% лучших SHORT были после положительного ETF flow",
            among="winners_SHORT",
            pct=pct,
            n=n,
            base=len(short_winners),
        )

    # Direction mix
    for among, rows, label in (
        ("winners", winners, "прибыльных"),
        ("losers", losers, "убыточных"),
    ):
        if not rows:
            continue
        long_pct, ln = _rate(rows, lambda r: str(r.get("direction")) == "LONG")
        add(
            f"{long_pct:.0f}% {label} сделок — LONG",
            among=among,
            pct=long_pct,
            n=ln,
            base=len(rows),
        )

    # Exit reasons among losers
    for reason in ("STOP", "TIMEOUT", "TRAILING", "TP1", "TP2"):
        pct, n = _rate(losers, lambda r, rr=reason: str(r.get("exit_reason") or "").upper() == rr)
        add(
            f"{pct:.0f}% убытков закрылись по {reason}",
            among="losers",
            pct=pct,
            n=n,
            base=len(losers),
        )

    # Hour / symbol concentration among losers
    by_sym: dict[str, int] = defaultdict(int)
    for r in losers:
        by_sym[str(r.get("symbol") or "?")] += 1
    if losers and by_sym:
        top_sym, top_n = max(by_sym.items(), key=lambda x: x[1])
        pct = 100.0 * top_n / len(losers)
        add(
            f"{pct:.0f}% убытков на {top_sym}",
            among="losers",
            pct=pct,
            n=top_n,
            base=len(losers),
        )

    by_hour: dict[int, int] = defaultdict(int)
    for r in losers:
        if r.get("hour") is not None:
            by_hour[int(r["hour"])] += 1
    if losers and by_hour:
        h, hn = max(by_hour.items(), key=lambda x: x[1])
        pct = 100.0 * hn / len(losers)
        add(
            f"{pct:.0f}% убытков в час {h}",
            among="losers",
            pct=pct,
            n=hn,
            base=len(losers),
        )

    # High AI score losers (overconfidence)
    hi_ai_losers = [r for r in losers if (_safe_float(r.get("ai_score")) or 0) >= 0.7]
    if len(losers) >= S56_MIN_EVIDENCE and hi_ai_losers:
        pct = 100.0 * len(hi_ai_losers) / len(losers)
        add(
            f"{pct:.0f}% убытков имели AI Score ≥ 0.7",
            among="losers",
            pct=pct,
            n=len(hi_ai_losers),
            base=len(losers),
        )

    findings.sort(key=lambda x: (-x["pct"], -x["n"]))
    return {
        "winners_n": len(winners),
        "losers_n": len(losers),
        "findings": findings[:25],
        "what_unites_winners": [f for f in findings if f["among"].startswith("winners")][:8],
        "what_unites_losers": [f for f in findings if "loser" in f["among"]][:8],
    }


def _suggest_from_stats(
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
    importance: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Minimal rule proposals from numbers (no LLM). Never applied automatically.

    S56.2 default: disabled — prefer richer pattern tables over weak recommendations.
    Enable with S56_SUGGESTIONS_ENABLED=1 when analysis coverage is solid.
    """
    suggestions: list[dict[str, Any]] = []
    refresh_s56_config_from_env()
    if not S56_SUGGESTIONS_ENABLED:
        return suggestions

    long_losers = [r for r in losers if str(r.get("direction")) == "LONG"]
    if len(long_losers) >= S56_MIN_EVIDENCE:
        neg_f = [r for r in long_losers if (_safe_float(r.get("funding")) or 0) < -0.0002]
        if len(neg_f) / len(long_losers) >= 0.55:
            # Counterfactual approx: drop those trades' avg pnl drag
            drag = abs(sum(float(r.get("pnl_usd") or 0) for r in neg_f) / max(1, len(losers)))
            suggestions.append({
                "rule_text": "Disable LONG when Funding < -0.02%",
                "evidence_trades": len(neg_f),
                "expected_improvement_pct": round(min(15.0, drag / 10.0), 2),
                "confidence_pct": round(min(95.0, 55.0 + 100.0 * len(neg_f) / len(long_losers)), 1),
                "source": "deterministic_rca",
                "evidence_json": json.dumps({
                    "long_losers": len(long_losers),
                    "long_losers_neg_funding": len(neg_f),
                    "threshold": -0.0002,
                }),
            })

    # Symbol night block if concentrated losses in hours 0-4
    night_losers = [
        r for r in losers
        if r.get("hour") is not None and 0 <= int(r["hour"]) <= 4
    ]
    if len(losers) >= S56_MIN_EVIDENCE and night_losers and len(night_losers) / len(losers) >= 0.35:
        by_sym: dict[str, int] = defaultdict(int)
        for r in night_losers:
            by_sym[str(r.get("symbol") or "?")] += 1
        sym, sn = max(by_sym.items(), key=lambda x: x[1])
        if sn >= S56_MIN_EVIDENCE // 2:
            suggestions.append({
                "rule_text": f"Do not trade {sym} at night (hour 0-4 UTC/local)",
                "evidence_trades": sn,
                "expected_improvement_pct": round(min(10.0, 100.0 * sn / len(losers) * 0.15), 2),
                "confidence_pct": round(min(92.0, 50.0 + 40.0 * sn / len(night_losers)), 1),
                "source": "deterministic_rca",
                "evidence_json": json.dumps({"symbol": sym, "night_losses": sn, "all_losers": len(losers)}),
            })

    # Top feature thresholds
    for imp in importance[:3]:
        if not imp.get("stars"):
            continue
        feat = imp["feature"]
        if feat == "funding" and imp["delta"] > 0:
            suggestions.append({
                "rule_text": "Prefer setups with Funding closer to winners' average (avoid deep negative funding)",
                "evidence_trades": imp["n_winners"] + imp["n_losers"],
                "expected_improvement_pct": round(min(8.0, imp["effect_size"] * 3), 2),
                "confidence_pct": round(min(90.0, 40.0 + imp["effect_size"] * 40), 1),
                "source": "feature_importance",
                "evidence_json": json.dumps(imp),
            })
        if feat == "news_score" and imp["delta"] > 0:
            suggestions.append({
                "rule_text": "Require higher News Score (winners avg exceeds losers)",
                "evidence_trades": imp["n_winners"] + imp["n_losers"],
                "expected_improvement_pct": round(min(8.0, imp["effect_size"] * 3), 2),
                "confidence_pct": round(min(90.0, 40.0 + imp["effect_size"] * 40), 1),
                "source": "feature_importance",
                "evidence_json": json.dumps(imp),
            })

    # Dedup by rule_text
    seen = set()
    uniq = []
    for s in suggestions:
        if s["rule_text"] in seen:
            continue
        seen.add(s["rule_text"])
        uniq.append(s)
    return uniq[:10]


_LLM_SYSTEM = """You are a hedge-fund trading analyst.
You do NOT invent narratives. You use ONLY the provided numbers.
You do NOT change the strategy. You only propose minimal rules.
Answer in this exact structure:

1. Profitable features (with %)
2. Losing features (with %)
3. Proposed rules (max 5, each: IF ... THEN ...; expected_improvement_pct; confidence_pct; evidence_trades)
4. Confidence overall (0-100)
5. Missing data needed

Rules must be minimal and falsifiable. No fluff."""


def _call_llm_postmortem(
    conn: Any,
    *,
    rca: dict[str, Any],
    importance: list[dict[str, Any]],
    winners_n: int,
    losers_n: int,
) -> tuple[str, str]:
    """Returns (text, method)."""
    refresh_s56_config_from_env()
    if not S56_LLM_ENABLED:
        return _format_deterministic_analyst(rca, importance), "deterministic"

    try:
        from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
            claude_call_allowed,
            telegram_claude_session,
        )
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            ClaudeClientError,
            call_claude_g2,
            is_claude_configured,
        )
    except Exception as exc:
        return f"LLM unavailable: {exc}\n\n" + _format_deterministic_analyst(rca, importance), "deterministic_fallback"

    if not is_claude_configured():
        return "Claude not configured.\n\n" + _format_deterministic_analyst(rca, importance), "deterministic_fallback"

    ok, reason = claude_call_allowed()
    if not ok:
        return (
            f"Claude blocked ({reason}). Using deterministic analyst only.\n\n"
            + _format_deterministic_analyst(rca, importance)
        ), "deterministic_fallback"

    user = json.dumps(
        {
            "winners_n": winners_n,
            "losers_n": losers_n,
            "rca": rca,
            "feature_importance": importance[:15],
            "instruction": "Find statistically meaningful differences. Numbers only.",
        },
        default=str,
        indent=2,
    )
    try:
        with telegram_claude_session():
            resp = call_claude_g2(
                system=_LLM_SYSTEM,
                user_content=user,
                label="s56_postmortem",
                max_tokens=2048,
            )
        return resp.text.strip(), "claude"
    except Exception as exc:
        logger.warning("s56 LLM failed: %s", exc)
        return f"Claude error: {exc}\n\n" + _format_deterministic_analyst(rca, importance), "deterministic_fallback"


def _format_deterministic_analyst(rca: dict[str, Any], importance: list[dict[str, Any]]) -> str:
    lines = [
        "1. Profitable features",
    ]
    for f in rca.get("what_unites_winners") or []:
        lines.append(f"  - {f['statement']} (n={f['n']}/{f['base']})")
    if not rca.get("what_unites_winners"):
        lines.append("  - insufficient evidence")
    lines.append("2. Losing features")
    for f in rca.get("what_unites_losers") or []:
        lines.append(f"  - {f['statement']} (n={f['n']}/{f['base']})")
    if not rca.get("what_unites_losers"):
        lines.append("  - insufficient evidence")
    lines.append("3. Proposed rules — see WAITING_APPROVAL suggestions (not auto-applied)")
    lines.append("4. Confidence overall — based on sample sizes only")
    lines.append(
        f"  winners={rca.get('winners_n')} losers={rca.get('losers_n')}"
    )
    lines.append("5. Missing data needed")
    missing = [x["feature"] for x in importance if x.get("n_winners", 0) < 20]
    sparse = [
        f for f in (*_NUMERIC_FEATURES, *_CATEGORICAL_FEATURES)
        if f not in {i["feature"] for i in importance}
    ]
    lines.append(f"  - sparse/unused dims: {', '.join(sparse[:12]) or 'none'}")
    lines.append("Feature importance:")
    for i in importance[:12]:
        if i.get("kind") == "categorical":
            lines.append(
                f"  {i['feature']}: {i['stars'] or '·'} effect={i['effect_size']} "
                f"top={i.get('top_category')} cats={i.get('n_categories')}"
            )
        else:
            lines.append(
                f"  {i['feature']}: {i['stars'] or '·'} effect={i['effect_size']} "
                f"W={i['winners_avg']} L={i['losers_avg']}"
            )
    return "\n".join(lines)


def run_postmortem(conn: Any, *, now: int | None = None, force: bool = False) -> dict[str, Any]:
    """Full postmortem: deep stats + RCA + importance + optional LLM + suggestions."""
    refresh_s56_config_from_env()
    now = int(now if now is not None else time.time())
    winners = top_winners(conn)
    losers = top_losers(conn)
    deep = compute_deep_stats(conn)
    rca = compute_rca(winners, losers)
    rca["deep_stats"] = {
        "overall": deep.get("overall"),
        "coverage": deep.get("coverage"),
        "symbols_top_n": len(deep.get("symbols_top") or []),
        "exits": deep.get("exits"),
    }
    importance = compute_feature_importance(winners, losers)
    llm_text, method = _call_llm_postmortem(
        conn, rca=rca, importance=importance, winners_n=len(winners), losers_n=len(losers),
    )
    closed_n = closed_trade_count(conn)

    cur = execute_with_retry(
        conn,
        f"""
        INSERT INTO {_RUNS} (
          closed_count, winners_n, losers_n, rca_json, feature_importance_json,
          llm_text, llm_method, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            closed_n,
            len(winners),
            len(losers),
            json.dumps(rca, default=str),
            json.dumps(importance, default=str),
            llm_text,
            method,
            now,
        ),
    )
    run_id = None
    try:
        run_id = int(cur.lastrowid)
    except Exception:
        try:
            run_id = int(conn.execute(f"SELECT MAX(id) AS i FROM {_RUNS}").fetchone()["i"])
        except Exception:
            run_id = None

    suggestions = _suggest_from_stats(winners, losers, importance)
    for s in suggestions:
        execute_with_retry(
            conn,
            f"""
            INSERT INTO {_SUGGEST} (
              run_id, rule_text, evidence_json, evidence_trades,
              expected_improvement_pct, confidence_pct, status, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                s["rule_text"],
                s.get("evidence_json"),
                int(s.get("evidence_trades") or 0),
                float(s.get("expected_improvement_pct") or 0),
                float(s.get("confidence_pct") or 0),
                STATUS_WAITING,
                s.get("source") or "deterministic_rca",
                now,
            ),
        )

    _ops_set(conn, "last_postmortem_closed_count", str(closed_n), now=now)
    _ops_set(conn, "last_postmortem_run_id", str(run_id or 0), now=now)

    return {
        "run_id": run_id,
        "closed_count": closed_n,
        "winners_n": len(winners),
        "losers_n": len(losers),
        "rca": rca,
        "deep_stats": deep,
        "feature_importance": importance,
        "llm_method": method,
        "llm_text": llm_text,
        "suggestions_created": len(suggestions),
        "suggestions_enabled": S56_SUGGESTIONS_ENABLED,
        "forced": force,
    }


def maybe_run_postmortem(conn: Any, *, now: int | None = None) -> dict[str, Any] | None:
    """Trigger every S56_RCA_EVERY_N closed trades."""
    refresh_s56_config_from_env()
    closed_n = closed_trade_count(conn)
    try:
        last = int(_ops_get(conn, "last_postmortem_closed_count", "0"))
    except ValueError:
        last = 0
    if closed_n < S56_RCA_EVERY_N:
        return None
    if closed_n - last < S56_RCA_EVERY_N and last > 0:
        return None
    # Also require enough snapshots
    try:
        snap_n = snapshot_count(conn)
    except Exception:
        snap_n = 0
    if snap_n < max(50, S56_MIN_EVIDENCE * 2):
        return None
    return run_postmortem(conn, now=now, force=False)


def list_suggestions(
    conn: Any,
    *,
    status: str | None = STATUS_WAITING,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            f"""
            SELECT * FROM {_SUGGEST}
            WHERE status = ?
            ORDER BY id DESC LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM {_SUGGEST} ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row(r) for r in rows]


def approve_suggestion(conn: Any, suggestion_id: int, *, now: int | None = None) -> dict[str, Any]:
    """Human approval only — does NOT change trading code."""
    now = int(now if now is not None else time.time())
    row = conn.execute(
        f"SELECT * FROM {_SUGGEST} WHERE id = ?",
        (suggestion_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": "not_found"}
    task = (
        f"S56.{suggestion_id}\n"
        f"Implement ONLY this rule (nothing else):\n"
        f"{row['rule_text']}\n"
        f"Evidence trades={row['evidence_trades']} "
        f"Expected improvement={row['expected_improvement_pct']}% "
        f"Confidence={row['confidence_pct']}%"
    )
    execute_with_retry(
        conn,
        f"""
        UPDATE {_SUGGEST}
        SET status = ?, decided_at = ?, cursor_task = ?
        WHERE id = ?
        """,
        (STATUS_APPROVED, now, task, suggestion_id),
    )
    return {"ok": True, "id": suggestion_id, "status": STATUS_APPROVED, "cursor_task": task}


def reject_suggestion(conn: Any, suggestion_id: int, *, now: int | None = None) -> dict[str, Any]:
    now = int(now if now is not None else time.time())
    cur = execute_with_retry(
        conn,
        f"""
        UPDATE {_SUGGEST}
        SET status = ?, decided_at = ?
        WHERE id = ? AND status = ?
        """,
        (STATUS_REJECTED, now, suggestion_id, STATUS_WAITING),
    )
    return {"ok": (cur.rowcount or 0) > 0, "id": suggestion_id, "status": STATUS_REJECTED}


def format_suggestion(s: dict[str, Any]) -> str:
    return "\n".join([
        f"Suggestion #{s.get('id')}",
        f"Rule",
        f"  {s.get('rule_text')}",
        f"Evidence",
        f"  {s.get('evidence_trades')} trades",
        f"Expected Improvement",
        f"  +{float(s.get('expected_improvement_pct') or 0):.1f}%",
        f"Confidence",
        f"  {float(s.get('confidence_pct') or 0):.0f}%",
        f"Status",
        f"  {s.get('status')}",
        f"Source",
        f"  {s.get('source')}",
    ])


def _pnl_by_key(rows: list[dict[str, Any]], key: str) -> list[tuple[str, float, int]]:
    agg: dict[str, float] = defaultdict(float)
    cnt: dict[str, int] = defaultdict(int)
    for r in rows:
        k = str(r.get(key) if r.get(key) is not None else "NULL")
        agg[k] += float(r.get("pnl_usd") or 0)
        cnt[k] += 1
    return sorted(((k, v, cnt[k]) for k, v in agg.items()), key=lambda x: x[1], reverse=True)


def snapshot_highlights(conn: Any) -> dict[str, Any]:
    empty = {
        "top_winner": None,
        "top_loser": None,
        "best_symbol": None,
        "worst_symbol": None,
        "best_hour": None,
        "worst_hour": None,
        "best_ai_score": None,
        "worst_ai_score": None,
    }
    try:
        rows = [_row(r) for r in conn.execute(f"SELECT * FROM {_SNAP} WHERE pnl_usd IS NOT NULL").fetchall()]
    except Exception:
        return empty
    if not rows:
        return empty

    winners = [r for r in rows if float(r.get("pnl_usd") or 0) > 0]
    losers = [r for r in rows if float(r.get("pnl_usd") or 0) < 0]
    top_w = max(winners, key=lambda r: float(r.get("pnl_usd") or 0)) if winners else None
    top_l = min(losers, key=lambda r: float(r.get("pnl_usd") or 0)) if losers else None
    by_sym = _pnl_by_key(rows, "symbol")
    by_hour = _pnl_by_key(rows, "hour")
    with_ai = [r for r in rows if r.get("ai_score") is not None]
    best_ai = max(with_ai, key=lambda r: float(r.get("pnl_usd") or 0)) if with_ai else None
    worst_ai = min(with_ai, key=lambda r: float(r.get("pnl_usd") or 0)) if with_ai else None

    def _fmt_trade(r: dict[str, Any] | None) -> str | None:
        if not r:
            return None
        return (
            f"{r.get('symbol')} {r.get('direction')} "
            f"${float(r.get('pnl_usd') or 0):+.2f} exit={r.get('exit_reason')} "
            f"ai={r.get('ai_score')}"
        )

    return {
        "top_winner": _fmt_trade(top_w),
        "top_loser": _fmt_trade(top_l),
        "best_symbol": f"{by_sym[0][0]} ${by_sym[0][1]:+.2f} (n={by_sym[0][2]})" if by_sym else None,
        "worst_symbol": f"{by_sym[-1][0]} ${by_sym[-1][1]:+.2f} (n={by_sym[-1][2]})" if by_sym else None,
        "best_hour": f"{by_hour[0][0]} ${by_hour[0][1]:+.2f} (n={by_hour[0][2]})" if by_hour else None,
        "worst_hour": f"{by_hour[-1][0]} ${by_hour[-1][1]:+.2f} (n={by_hour[-1][2]})" if by_hour else None,
        "best_ai_score": (
            f"ai={best_ai.get('ai_score')} pnl=${float(best_ai.get('pnl_usd') or 0):+.2f} {best_ai.get('symbol')}"
            if best_ai else None
        ),
        "worst_ai_score": (
            f"ai={worst_ai.get('ai_score')} pnl=${float(worst_ai.get('pnl_usd') or 0):+.2f} {worst_ai.get('symbol')}"
            if worst_ai else None
        ),
    }


def doctor_s56_status(conn: Any, *, live_conn: Any | None = None) -> dict[str, Any]:
    snap_n = snapshot_count(conn)
    waiting = list_suggestions(conn, status=STATUS_WAITING, limit=500)
    last = None
    try:
        last = conn.execute(f"SELECT * FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        pass
    last_txt = "—"
    if last:
        ts = int(last["created_at"] or 0)
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
        last_txt = f"#{last['id']} {when} W={last['winners_n']} L={last['losers_n']} ({last['llm_method']})"
    diag = diagnose_snapshots(conn, live_conn=live_conn)
    return {
        "snapshots": snap_n,
        "last_rca": last_txt,
        "suggestions_waiting": len(waiting),
        "closed_s42": diag.get("closed_s42", 0),
        "missing": diag.get("missing", 0),
        "ok": snap_n > 0 or int(diag.get("closed_s42") or 0) == 0,
    }


def _fmt_pf(m: dict[str, Any]) -> str:
    if m.get("pf_inf"):
        return "inf"
    pf = m.get("pf")
    if pf is None:
        return "—"
    return f"{pf:.2f}"


def _fmt_money(v: Any) -> str:
    if v is None:
        return "—"
    return f"${float(v):+.2f}"


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v):.1f}%"


def format_deep_stats_section(deep: dict[str, Any]) -> list[str]:
    """Human-readable S56.2 pattern tables."""
    lines: list[str] = []
    cov = deep.get("coverage") or {}
    overall = deep.get("overall") or {}
    lines.extend([
        "",
        "S56.2 Deep Stats",
        f"  snapshots={cov.get('snapshots', 0)}  symbols={cov.get('symbols', 0)}  "
        f"funding={cov.get('with_funding', 0)}  regime={cov.get('with_market_regime', 0)}  "
        f"news={cov.get('with_news_score', 0)}  macro={cov.get('with_macro_score', 0)}",
        f"  Overall: n={overall.get('n', 0)}  PnL={_fmt_money(overall.get('pnl'))}  "
        f"WR={_fmt_pct(overall.get('winrate'))}  "
        f"E={_fmt_money(overall.get('expectancy'))}  PF={_fmt_pf(overall)}",
        "",
        f"Symbols TOP-{len(deep.get('symbols_top') or [])} "
        "(by volume, sorted worst→best PnL)",
        f"  {'Symbol':<10} {'n':>5} {'PnL':>10} {'WR':>7} {'E':>9} {'PF':>6} "
        f"{'AvgW':>9} {'AvgL':>9}",
    ])
    for row in deep.get("symbols_top") or []:
        lines.append(
            f"  {str(row.get('symbol')):<10} {row.get('n'):>5} "
            f"{_fmt_money(row.get('pnl')):>10} {_fmt_pct(row.get('winrate')):>7} "
            f"{_fmt_money(row.get('expectancy')):>9} {_fmt_pf(row):>6} "
            f"{_fmt_money(row.get('avg_win')):>9} {_fmt_money(row.get('avg_loss')):>9}"
        )

    lines.extend([
        "",
        "Hours (0–23 UTC)",
        f"  {'H':>2} {'n':>5} {'PnL':>10} {'WR':>7} {'E':>9}",
    ])
    for row in deep.get("hours") or []:
        if int(row.get("n") or 0) == 0:
            continue
        lines.append(
            f"  {int(row['hour']):>2} {row.get('n'):>5} "
            f"{_fmt_money(row.get('pnl')):>10} {_fmt_pct(row.get('winrate')):>7} "
            f"{_fmt_money(row.get('expectancy')):>9}"
        )

    lines.extend([
        "",
        "Symbol × Direction (LONG / SHORT)",
        f"  {'Symbol':<10} {'Dir':<6} {'n':>5} {'PnL':>10} {'WR':>7} {'E':>9} {'PF':>6}",
    ])
    for row in deep.get("symbol_direction") or []:
        lines.append(
            f"  {str(row.get('symbol')):<10} {str(row.get('direction')):<6} "
            f"{row.get('n'):>5} {_fmt_money(row.get('pnl')):>10} "
            f"{_fmt_pct(row.get('winrate')):>7} {_fmt_money(row.get('expectancy')):>9} "
            f"{_fmt_pf(row):>6}"
        )

    lines.extend([
        "",
        "Exit reasons",
        f"  {'Exit':<18} {'n':>5} {'PnL':>10} {'WR':>7} {'E':>9}",
    ])
    for row in deep.get("exits") or []:
        if int(row.get("n") or 0) == 0:
            continue
        lines.append(
            f"  {str(row.get('exit_reason')):<18} {row.get('n'):>5} "
            f"{_fmt_money(row.get('pnl')):>10} {_fmt_pct(row.get('winrate')):>7} "
            f"{_fmt_money(row.get('expectancy')):>9}"
        )
    return lines


def format_postmortem_report(conn: Any) -> str:
    refresh_s56_config_from_env()
    diag = diagnose_snapshots(conn)
    hl = snapshot_highlights(conn)
    deep = compute_deep_stats(conn)
    winners = top_winners(conn, n=min(20, S56_TOP_N))
    losers = top_losers(conn, n=min(20, S56_TOP_N))
    w_n = len(top_winners(conn))
    l_n = len(top_losers(conn))
    waiting = list_suggestions(conn, status=STATUS_WAITING, limit=20)
    last = None
    try:
        last = conn.execute(f"SELECT * FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        pass
    lines = [
        "S56 Trade Post-Mortem",
        f"TOP winners available: {w_n} (cap {S56_TOP_N})",
        f"TOP losers available:  {l_n} (cap {S56_TOP_N})",
        f"RCA every N closed:    {S56_RCA_EVERY_N}",
        f"LLM enabled:           {S56_LLM_ENABLED}",
        f"Suggestions enabled:   {S56_SUGGESTIONS_ENABLED}",
        f"Closed S42:            {diag['closed_s42']}",
        f"Snapshots:             {diag['snapshots']}  (missing={diag['missing']})",
    ]
    for reason in diag.get("reasons") or []:
        lines.append(f"  → {reason}")
    if diag.get("missing"):
        lines.append(f"  fix: {diag.get('fix')}")
    lines.extend([
        "",
        "Highlights",
        f"  Top Winner:             {hl.get('top_winner') or '—'}",
        f"  Top Loser:              {hl.get('top_loser') or '—'}",
        f"  Most profitable symbol: {hl.get('best_symbol') or '—'}",
        f"  Worst symbol:           {hl.get('worst_symbol') or '—'}",
        f"  Most profitable hour:   {hl.get('best_hour') or '—'}",
        f"  Worst hour:             {hl.get('worst_hour') or '—'}",
        f"  Best AI score trade:    {hl.get('best_ai_score') or '—'}",
        f"  Worst AI score trade:   {hl.get('worst_ai_score') or '—'}",
    ])
    lines.extend(format_deep_stats_section(deep))
    lines.extend([
        "",
        "Sample TOP winners (by PnL $):",
    ])
    for i, r in enumerate(winners[:10], 1):
        lines.append(
            f"  {i}. {r.get('symbol')} {r.get('direction')} "
            f"${float(r.get('pnl_usd') or 0):+.2f} exit={r.get('exit_reason')} "
            f"fund={r.get('funding')} ai={r.get('ai_score')}"
        )
    lines.append("Sample TOP losers:")
    for i, r in enumerate(losers[:10], 1):
        lines.append(
            f"  {i}. {r.get('symbol')} {r.get('direction')} "
            f"${float(r.get('pnl_usd') or 0):+.2f} exit={r.get('exit_reason')} "
            f"fund={r.get('funding')} ai={r.get('ai_score')}"
        )
    if last:
        lines.extend([
            "",
            f"Last run #{last['id']} method={last['llm_method']} "
            f"W={last['winners_n']} L={last['losers_n']}",
        ])
        try:
            imp = json.loads(last["feature_importance_json"] or "[]")
            lines.append("Feature importance:")
            for i in imp[:12]:
                if i.get("kind") == "categorical":
                    lines.append(
                        f"  {i.get('feature')}: {i.get('stars') or '·'} "
                        f"({i.get('effect_size')}) top={i.get('top_category')}"
                    )
                else:
                    lines.append(
                        f"  {i.get('feature')}: {i.get('stars') or '·'} ({i.get('effect_size')})"
                    )
        except Exception:
            pass
        try:
            rca = json.loads(last["rca_json"] or "{}")
            lines.append("RCA (losers):")
            for f in (rca.get("what_unites_losers") or [])[:5]:
                lines.append(f"  - {f.get('statement')}")
        except Exception:
            pass
    lines.extend([
        "",
        f"Suggestions WAITING_APPROVAL: {len(waiting)}"
        + ("" if S56_SUGGESTIONS_ENABLED else "  (generation OFF — set S56_SUGGESTIONS_ENABLED=1)"),
    ])
    for s in waiting[:5]:
        lines.append("")
        lines.append(format_suggestion(s))
    lines.extend([
        "",
        "Human approval required. LLM/stats never change strategy.",
        "Approve: python -m bot.research.market_events approve-suggestion --suggestion-id N",
        "Backfill: python -m bot.research.market_events trade-postmortem --backfill",
    ])
    return "\n".join(lines)


def format_s56_report_block(conn: Any) -> list[str]:
    refresh_s56_config_from_env()
    snap_n = snapshot_count(conn)
    waiting = list_suggestions(conn, status=STATUS_WAITING, limit=100)
    hl = snapshot_highlights(conn)
    diag = diagnose_snapshots(conn)
    deep = compute_deep_stats(conn)
    overall = deep.get("overall") or {}
    exits_nonzero = [e for e in (deep.get("exits") or []) if int(e.get("n") or 0) > 0]
    worst_syms = (deep.get("symbols_top") or [])[:3]
    best_hour = None
    worst_hour = None
    hours_nz = [h for h in (deep.get("hours") or []) if int(h.get("n") or 0) > 0]
    if hours_nz:
        best_hour = max(hours_nz, key=lambda x: x.get("pnl") or 0)
        worst_hour = min(hours_nz, key=lambda x: x.get("pnl") or 0)
    lines = [
        "",
        "S56 Post-Mortem",
        f"  snapshots={snap_n}  waiting_approval={len(waiting)}  "
        f"rca_every={S56_RCA_EVERY_N}  llm={S56_LLM_ENABLED}  "
        f"suggestions={S56_SUGGESTIONS_ENABLED}",
        f"  Overall: n={overall.get('n', 0)}  PnL={_fmt_money(overall.get('pnl'))}  "
        f"WR={_fmt_pct(overall.get('winrate'))}  E={_fmt_money(overall.get('expectancy'))}  "
        f"PF={_fmt_pf(overall)}",
        f"  Top Winner:             {hl.get('top_winner') or '—'}",
        f"  Top Loser:              {hl.get('top_loser') or '—'}",
        f"  Most profitable symbol: {hl.get('best_symbol') or '—'}",
        f"  Worst symbol:           {hl.get('worst_symbol') or '—'}",
        f"  Most profitable hour:   {hl.get('best_hour') or '—'}",
        f"  Worst hour:             {hl.get('worst_hour') or '—'}",
        f"  Best AI score:          {hl.get('best_ai_score') or '—'}",
        f"  Worst AI score:         {hl.get('worst_ai_score') or '—'}",
    ]
    if worst_syms:
        lines.append("  Symbols (worst→):")
        for row in worst_syms:
            lines.append(
                f"    {row.get('symbol')}: n={row.get('n')} PnL={_fmt_money(row.get('pnl'))} "
                f"WR={_fmt_pct(row.get('winrate'))} E={_fmt_money(row.get('expectancy'))} "
                f"PF={_fmt_pf(row)}"
            )
    if best_hour and worst_hour:
        lines.append(
            f"  Hours deep: best H{best_hour['hour']} PnL={_fmt_money(best_hour.get('pnl'))}  "
            f"worst H{worst_hour['hour']} PnL={_fmt_money(worst_hour.get('pnl'))}"
        )
    if exits_nonzero:
        bits = [
            f"{e.get('exit_reason')} n={e.get('n')} {_fmt_money(e.get('pnl'))}"
            for e in exits_nonzero[:6]
        ]
        lines.append(f"  Exits: {'; '.join(bits)}")
    lines.append("  Full tables: python -m bot.research.market_events trade-postmortem")
    if snap_n == 0 and int(diag.get("closed_s42") or 0) > 0:
        lines.append(f"  WARN: {diag['closed_s42']} closed trades, 0 snapshots — run --backfill")
    return lines


__all__ = [
    "STATUS_APPROVED",
    "STATUS_REJECTED",
    "STATUS_WAITING",
    "approve_suggestion",
    "backfill_snapshots",
    "bucket_metrics",
    "compute_deep_stats",
    "compute_feature_importance",
    "compute_rca",
    "diagnose_snapshots",
    "doctor_s56_status",
    "format_deep_stats_section",
    "format_postmortem_report",
    "format_s56_report_block",
    "format_suggestion",
    "list_suggestions",
    "load_all_snapshots",
    "maybe_run_postmortem",
    "record_close_snapshot",
    "reject_suggestion",
    "refresh_s56_config_from_env",
    "run_postmortem",
    "snapshot_count",
    "snapshot_highlights",
    "top_losers",
    "top_winners",
]
