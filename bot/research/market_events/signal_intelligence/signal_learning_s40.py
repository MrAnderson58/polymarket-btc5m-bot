"""Phase S4.0 — Observe-only signal learning pipeline.

This layer is additive and must not change Decision/Market/News/Pattern logic.
It only:
  1) stores a market snapshot at signal creation,
  2) stores horizon checkpoints (15m/1h/4h/24h),
  3) after close generates text-only Claude self-review (if configured).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable

from bot.research.market_events.db import (
    execute_with_retry,
    insert_returning_id,
    is_database_locked,
    market_events_connection,
    market_events_readonly_connection,
)
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
    claude_call_allowed,
)
from bot.research.market_events.signal_intelligence.claude_client_g2 import (
    ClaudeClientError,
    call_claude_g2,
    is_claude_configured,
)

logger = logging.getLogger(__name__)

HORIZONS_S40: tuple[tuple[str, int], ...] = (
    ("15m", 15 * 60),
    ("1h", 60 * 60),
    ("4h", 4 * 60 * 60),
    ("24h", 24 * 60 * 60),
)

MAX_REVIEWS_PER_CYCLE = 5
MAX_LOCAL_REVIEWS_PER_CYCLE = 100
QUEUE_NEWEST_PER_CYCLE = 2
QUEUE_OLDEST_PER_CYCLE = 3
CLAUDE_REVIEW_TIMEOUT_SEC = 15
DAILY_PERFORMANCE_HOUR = 22
WORKER_RUNNING_WINDOW_SEC = 150
REVIEW_STATUS_COMPLETE = "complete"
REVIEW_STATUS_PENDING_AI = "pending_ai"
REVIEW_TYPE_CLAUDE = "claude"
REVIEW_TYPE_LOCAL = "local"
REVIEW_TYPE_PLACEHOLDER = "placeholder"
PLACEHOLDER_REASON_CLAUDE_UNAVAILABLE = "claude_unavailable"
PLACEHOLDER_REASON_TIMEOUT = "timeout"
PLACEHOLDER_REASON_INVALID_JSON = "invalid_json"
PLACEHOLDER_REASON_OTHER = "other"


def _exec_sql(conn: Any, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Any:
    """Execute SQL; on failure log the full statement for worker diagnostics."""
    try:
        return conn.execute(sql, params)
    except Exception:
        logger.exception("S4 worker failed. SQL=%s params=%s", sql.strip(), params)
        raise


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pnl_pct(entry: float, price: float, *, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (price / entry - 1.0) * 100.0
    return (1.0 - price / entry) * 100.0


def _win_loss_be(pnl_pct: float | None) -> str:
    v = float(pnl_pct or 0)
    if v > 0:
        return "WIN"
    if v < 0:
        return "LOSS"
    return "BE"


def _parse_json_maybe(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _extract_fear_greed_from_any(raw_json: str | None) -> float | None:
    """Best-effort fear/greed extraction from JSON-ish blobs."""
    d = _parse_json_maybe(raw_json)
    for k in ("fear_greed", "fearGreed", "fear_greed_value", "fear_greed_index", "fear"):
        if k in d:
            return _safe_float(d.get(k))
    return None


def _tp_sl_from_g32_rr(*, entry: float, rr: float, direction: str) -> tuple[float | None, float | None]:
    # Must not affect trading logic — only for learning snapshots.
    from bot.research.market_events.signal_intelligence.config import G32_DEFAULT_STOP_PCT

    stop_pct = float(G32_DEFAULT_STOP_PCT or 0.0)
    rr_val = float(rr or 0.0)
    if rr_val <= 0:
        rr_val = 2.5
    tp_pct = min(5.0, stop_pct * rr_val * 0.45)  # matches replay_g32.py

    is_long = direction.upper() == "LONG"
    if entry <= 0:
        return None, None
    if is_long:
        sl = entry * (1.0 - stop_pct / 100.0)
        tp1 = entry * (1.0 + tp_pct / 100.0)
    else:
        sl = entry * (1.0 + stop_pct / 100.0)
        tp1 = entry * (1.0 - tp_pct / 100.0)
    return tp1, sl


@dataclass(frozen=True)
class S40SignalKey:
    signal_type: str
    signal_id: int


def _ensure_ops_state(conn: Any) -> None:
    now = int(time.time())
    execute_with_retry(
        conn,
        """
        INSERT OR IGNORE INTO market_events_signal_learning_s40_ops_state (key, value, updated_at)
        VALUES (?, ?, ?)
        """,
        ("last_ingest_ts", "0", now),
    )


def _get_ops_state(conn: Any, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM market_events_signal_learning_s40_ops_state WHERE key = ?",
        (key,),
    ).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def _set_ops_state(conn: Any, key: str, value: str) -> None:
    now = int(time.time())
    execute_with_retry(
        conn,
        """
        INSERT OR REPLACE INTO market_events_signal_learning_s40_ops_state (key, value, updated_at)
        VALUES (?, ?, ?)
        """,
        (key, value, now),
    )


def _incr_ops_counter(conn: Any, key: str, delta: int = 1) -> None:
    if delta <= 0:
        return
    prev_raw = _get_ops_state(conn, key) or "0"
    try:
        prev = int(prev_raw)
    except (TypeError, ValueError):
        prev = 0
    _set_ops_state(conn, key, str(prev + delta))


def _infer_placeholder_reason(
    analysis_text: str | None,
    *,
    stored_reason: str | None = None,
) -> str:
    if stored_reason:
        return stored_reason
    text = (analysis_text or "").strip()
    if not text:
        return PLACEHOLDER_REASON_TIMEOUT
    if text.startswith("Claude unavailable."):
        return PLACEHOLDER_REASON_CLAUDE_UNAVAILABLE
    lowered = text.lower()
    if "invalid json" in lowered or "jsondecodeerror" in lowered:
        return PLACEHOLDER_REASON_INVALID_JSON
    return PLACEHOLDER_REASON_OTHER


def ingest_new_s40_signals(conn: Any, *, limit: int = 200) -> int:
    """Populate market_events_signal_learning_s40_signals for new signals."""
    now = int(time.time())

    last_ingest_raw = _get_ops_state(conn, "last_ingest_ts") or "0"
    last_ingest_ts = int(last_ingest_raw)

    n_written = 0

    # 1) G3 live signals (g3_signal lane)
    g3_rows = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, tp1, tp2, sl, created_at,
               confidence, probability, market_score, risk_reward,
               snapshot_id, event_id, trade_plan_json, reason_json, historical_json, claude_summary
        FROM market_live_signals_g3
        WHERE created_at >= ? OR created_at = (SELECT MAX(created_at) FROM market_live_signals_g3)
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (last_ingest_ts, limit),
    ).fetchall()
    existing_keys = set(
        r["signal_id"]
        for r in conn.execute(
            "SELECT signal_id FROM market_events_signal_learning_s40_signals WHERE signal_type = ?",
            ("g3_signal",),
        ).fetchall()
    )

    for r in g3_rows:
        sid = int(r["id"])
        if sid in existing_keys:
            continue

        snap = conn.execute(
            """
            SELECT funding, open_interest, volume, atr, fear_greed
            FROM market_snapshots_g3
            WHERE id = ?
            """,
            (r["snapshot_id"],),
        ).fetchone()

        trend_row = conn.execute(
            """
            SELECT trend_score, direction
            FROM market_trend_windows_g3
            WHERE snapshot_id = ?
            ORDER BY window_minutes DESC, created_at DESC
            LIMIT 1
            """,
            (r["snapshot_id"],),
        ).fetchone()

        f7 = conn.execute(
            """
            SELECT news_impact, final_confidence
            FROM market_events_market_intelligence_f7
            WHERE event_id = ?
            """,
            (r["event_id"],),
        ).fetchone()

        # Pattern: keep it as opaque JSON text for later analysis.
        trade_plan_raw = r["trade_plan_json"]
        reason_raw = r["reason_json"]
        historical_raw = r["historical_json"]
        claude_summary_raw = r["claude_summary"]
        pattern_blob = {
            "trade_plan": json.loads(trade_plan_raw) if trade_plan_raw else {},
            "reason": json.loads(reason_raw) if reason_raw else [],
            "historical": json.loads(historical_raw) if historical_raw else [],
            "claude_summary": claude_summary_raw or "",
        }
        created_at = int(r["created_at"])
        execute_with_retry(
            conn,
            """
            INSERT OR IGNORE INTO market_events_signal_learning_s40_signals (
                signal_type, signal_id, symbol, direction,
                entry, stop, tp1, tp2, timestamp,
                snapshot_funding, snapshot_open_interest, snapshot_volume, snapshot_atr, snapshot_fear_greed,
                snapshot_trend, snapshot_news_score, snapshot_news_impact,
                snapshot_pattern_json, snapshot_decision_confidence,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "g3_signal",
                sid,
                str(r["symbol"]),
                str(r["direction"]),
                _safe_float(r["entry_price"]),
                _safe_float(r["sl"]),
                _safe_float(r["tp1"]),
                _safe_float(r["tp2"]),
                created_at,
                _safe_float(snap["funding"]) if snap else None,
                _safe_float(snap["open_interest"]) if snap else None,
                _safe_float(snap["volume"]) if snap else None,
                _safe_float(snap["atr"]) if snap else None,
                _safe_float(snap["fear_greed"]) if snap else None,
                (f"{trend_row['direction']}:{trend_row['trend_score']}" if trend_row else None),
                None,  # numeric news score not guaranteed in schema
                str(f7["news_impact"]) if f7 and f7["news_impact"] is not None else None,
                json.dumps(pattern_blob, ensure_ascii=False),
                float(f7["final_confidence"]) if f7 and f7["final_confidence"] is not None else float(r["confidence"]),
                created_at,
                now,
            ),
        )
        n_written += 1

    # 2) Telegram outcome signals (telegram lane, derived from f72)
    existing_keys_tel = set(
        r["signal_id"]
        for r in conn.execute(
            """
            SELECT signal_id
            FROM market_events_signal_learning_s40_signals
            WHERE signal_type = ?
            """,
            ("telegram_signal",),
        ).fetchall()
    )

    tel_rows = conn.execute(
        """
        SELECT event_id, symbol, trade_side, entry, tp1, tp2, tp3, sl, entry_time,
               exit_time, exit_reason,
               pnl_pct, holding_seconds, risk_reward, signal_score, market_score,
               pattern_json, tp1_hit_time, tp2_hit_time, sl_hit_time,
               author_channel, created_at
        FROM market_events_signal_outcomes_f72
        WHERE created_at >= ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (last_ingest_ts, limit),
    ).fetchall()

    for r in tel_rows:
        sid = int(r["event_id"])
        if sid in existing_keys_tel:
            continue

        exch = conn.execute(
            """
            SELECT funding, open_interest, volume_24h, atr, raw_json
            FROM market_event_exchange_context
            WHERE event_id = ?
            LIMIT 1
            """,
            (sid,),
        ).fetchone()

        fear_greed = _extract_fear_greed_from_any(exch["raw_json"]) if exch else None
        f7 = conn.execute(
            """
            SELECT news_impact, final_confidence, long_trend_stage
            FROM market_events_market_intelligence_f7
            WHERE event_id = ?
            """,
            (sid,),
        ).fetchone()

        decision_conf = float(f7["final_confidence"]) if f7 and f7["final_confidence"] is not None else float(r["signal_score"] or 0)
        trend_label = str(f7["long_trend_stage"]) if f7 and f7["long_trend_stage"] is not None else None

        created_at = int(r["created_at"])
        execute_with_retry(
            conn,
            """
            INSERT OR IGNORE INTO market_events_signal_learning_s40_signals (
                signal_type, signal_id, symbol, direction,
                entry, stop, tp1, tp2, timestamp,
                snapshot_funding, snapshot_open_interest, snapshot_volume, snapshot_atr, snapshot_fear_greed,
                snapshot_trend, snapshot_news_score, snapshot_news_impact,
                snapshot_pattern_json, snapshot_decision_confidence,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "telegram_signal",
                sid,
                str(r["symbol"]),
                str(r["trade_side"]),
                _safe_float(r["entry"]),
                _safe_float(r["sl"]),
                _safe_float(r["tp1"]),
                _safe_float(r["tp2"]),
                int(r["entry_time"] or created_at),
                _safe_float(exch["funding"]) if exch else None,
                _safe_float(exch["open_interest"]) if exch else None,
                _safe_float(exch["volume_24h"]) if exch else None,
                _safe_float(exch["atr"]) if exch else None,
                fear_greed,
                trend_label,
                None,
                str(f7["news_impact"]) if f7 and f7["news_impact"] is not None else None,
                r["pattern_json"] if r["pattern_json"] is not None else "{}",
                decision_conf,
                created_at,
                now,
            ),
        )
        n_written += 1

    # 3) Validation records (validation lane)
    existing_keys_val = set(
        r["signal_id"]
        for r in conn.execute(
            "SELECT signal_id FROM market_events_signal_learning_s40_signals WHERE signal_type = ?",
            ("validation_signal",),
        ).fetchall()
    )
    val_rows = conn.execute(
        """
        SELECT id, source_type, source_id, symbol, direction, pnl_pct, rr,
               confidence, market_score, liquidity_score, factors_json, outcome_ts, created_at
        FROM market_validation_records_g4
        WHERE created_at >= ?
        ORDER BY outcome_ts DESC
        LIMIT ?
        """,
        (last_ingest_ts, limit),
    ).fetchall()
    for r in val_rows:
        sid = int(r["id"])
        if sid in existing_keys_val:
            continue

        # Best-effort: resolve entry/stop/tp from source records.
        entry = stop = tp1 = tp2 = None
        ts = int(r["outcome_ts"] or r["created_at"])
        snapshot: dict[str, Any] = {}
        direction = str(r["direction"] or "LONG")

        if r["source_type"] == "g3_signal":
            g3 = conn.execute(
                """
                SELECT entry_price, tp1, tp2, sl, snapshot_id, event_id, direction, confidence, trade_plan_json, reason_json, historical_json, claude_summary
                FROM market_live_signals_g3
                WHERE id = ?
                """,
                (int(r["source_id"]),),
            ).fetchone()
            if g3:
                entry = _safe_float(g3["entry_price"])
                stop = _safe_float(g3["sl"])
                tp1 = _safe_float(g3["tp1"])
                tp2 = _safe_float(g3["tp2"])
                direction = str(g3["direction"])
                ts = ts or int(r["created_at"])
                snap = conn.execute(
                    """
                    SELECT funding, open_interest, volume, atr, fear_greed
                    FROM market_snapshots_g3
                    WHERE id = ?
                    """,
                    (g3["snapshot_id"],),
                ).fetchone()
                trend_row = conn.execute(
                    """
                    SELECT trend_score, direction
                    FROM market_trend_windows_g3
                    WHERE snapshot_id = ?
                    ORDER BY window_minutes DESC, created_at DESC
                    LIMIT 1
                    """,
                    (g3["snapshot_id"],),
                ).fetchone()
                f7 = conn.execute(
                    """
                    SELECT news_impact, final_confidence, long_trend_stage
                    FROM market_events_market_intelligence_f7
                    WHERE event_id = ?
                    """,
                    (g3["event_id"],),
                ).fetchone()
                snapshot.update({
                    "funding": _safe_float(snap["funding"]) if snap else None,
                    "open_interest": _safe_float(snap["open_interest"]) if snap else None,
                    "volume": _safe_float(snap["volume"]) if snap else None,
                    "atr": _safe_float(snap["atr"]) if snap else None,
                    "fear_greed": _safe_float(snap["fear_greed"]) if snap else None,
                    "trend": f"{trend_row['direction']}:{trend_row['trend_score']}" if trend_row else None,
                    "news_impact": str(f7["news_impact"]) if f7 and f7["news_impact"] is not None else None,
                    "news_confidence": float(f7["final_confidence"]) if f7 and f7["final_confidence"] is not None else float(g3["confidence"]),
                    "pattern": json.dumps({
                        "trade_plan": json.loads(g3["trade_plan_json"]) if g3["trade_plan_json"] else {},
                        "reason": json.loads(g3["reason_json"]) if g3["reason_json"] else [],
                        "historical": json.loads(g3["historical_json"]) if g3["historical_json"] else [],
                        "claude_summary": g3["claude_summary"] or "",
                    }, ensure_ascii=False),
                })

        else:
            # replay_complete / rejected_candidate — derive entry/stop/tp from outcome row + rr
            out = _exec_sql(
                conn,
                """
                SELECT o.price_entry, o.best_rr, COALESCE(o.direction, c.direction) AS direction,
                       c.funding_score, c.oi_score, c.volume_score, c.atr_score,
                       c.fear_greed, c.trend_score, c.candidate_state, c.rejection_reason, c.confidence
                FROM market_candidate_outcomes_g32 o
                JOIN market_candidate_g31 c ON c.id = o.candidate_id
                WHERE o.id = ?
                """,
                (int(r["source_id"]),),
            ).fetchone()
            if out:
                entry = _safe_float(out["price_entry"])
                rr = _safe_float(out["best_rr"]) or 0.0
                # direction from record is OK; fallback to candidate direction.
                direction = str(r["direction"] or out["direction"] or "LONG")
                tp1, stop = _tp_sl_from_g32_rr(entry=float(entry or 0), rr=float(rr), direction=direction)
                tp2 = None

                snapshot.update({
                    "funding": _safe_float(out["funding_score"]),
                    "open_interest": _safe_float(out["oi_score"]),
                    "volume": _safe_float(out["volume_score"]),
                    "atr": _safe_float(out["atr_score"]),
                    "fear_greed": _safe_float(out["fear_greed"]),
                    "trend": _safe_float(out["trend_score"]),
                    "news_impact": None,
                    "news_confidence": float(out["confidence"] or 0),
                    "pattern": json.dumps(
                        {
                            "candidate_state": out["candidate_state"],
                            "rejection_reason": out["rejection_reason"],
                        },
                        ensure_ascii=False,
                    ),
                })

        created_at = int(r["created_at"])
        execute_with_retry(
            conn,
            """
            INSERT OR IGNORE INTO market_events_signal_learning_s40_signals (
                signal_type, signal_id, symbol, direction,
                entry, stop, tp1, tp2, timestamp,
                snapshot_funding, snapshot_open_interest, snapshot_volume, snapshot_atr, snapshot_fear_greed,
                snapshot_trend, snapshot_news_score, snapshot_news_impact,
                snapshot_pattern_json, snapshot_decision_confidence,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "validation_signal",
                sid,
                str(r["symbol"]),
                direction,
                entry,
                stop,
                tp1,
                tp2,
                ts,
                snapshot.get("funding"),
                snapshot.get("open_interest"),
                snapshot.get("volume"),
                snapshot.get("atr"),
                snapshot.get("fear_greed"),
                snapshot.get("trend"),
                None,
                snapshot.get("news_impact"),
                snapshot.get("pattern")
                or json.dumps(
                    _parse_json_maybe(r["factors_json"] if r["factors_json"] is not None else ""),
                    ensure_ascii=False,
                ),
                snapshot.get("news_confidence") or _safe_float(r["confidence"]),
                created_at,
                now,
            ),
        )
        n_written += 1

    if n_written:
        _set_ops_state(conn, "last_ingest_ts", str(now))

    return n_written


def _ensure_checkpoint_for_signal(
    conn: Any,
    *,
    signal_type: str,
    signal_id: int,
    timestamp: int,
    horizon_key: str,
    horizon_s: int,
) -> None:
    now = int(time.time())
    checkpoint_ts = timestamp + horizon_s
    if now < checkpoint_ts:
        return

    exists = conn.execute(
        """
        SELECT 1
        FROM market_events_signal_learning_s40_checkpoints
        WHERE signal_type = ? AND signal_id = ? AND horizon_key = ?
        """,
        (signal_type, signal_id, horizon_key),
    ).fetchone()
    if exists:
        return

    row = conn.execute(
        """
        SELECT entry, stop, tp1, tp2
        FROM market_events_signal_learning_s40_signals
        WHERE signal_type = ? AND signal_id = ?
        """,
        (signal_type, signal_id),
    ).fetchone()
    if not row:
        return
    entry = float(row["entry"] or 0)
    sl = float(row["stop"] or 0) if row["stop"] is not None else None
    tp1 = float(row["tp1"] or 0) if row["tp1"] is not None else None
    tp2 = float(row["tp2"] or 0) if row["tp2"] is not None else None

    max_profit_pct = None
    max_drawdown_pct = None
    reached_tp1 = 0
    reached_tp2 = 0
    stopped = 0
    expired = 0

    is_long = True

    if signal_type == "g3_signal":
        g3 = conn.execute(
            """
            SELECT direction, status, max_profit_pct, max_drawdown_pct, tp1, tp2, sl, created_at,
                   entry_price, closed_at
            FROM market_live_signals_g3
            WHERE id = ?
            """,
            (signal_id,),
        ).fetchone()
        if not g3:
            return
        is_long = str(g3["direction"]).upper() == "LONG"
        max_profit_pct = _safe_float(g3["max_profit_pct"])
        max_drawdown_pct = _safe_float(g3["max_drawdown_pct"])

        # Accurate TP reach from followup timestamps.
        follow = conn.execute(
            """
            SELECT followup_type, created_at
            FROM market_signal_followup_g3
            WHERE signal_id = ?
            AND followup_type IN ('TP1', 'TP2', 'CLOSED')
            """,
            (signal_id,),
        ).fetchall()
        tp1_ts = next((int(f["created_at"]) for f in follow if f["followup_type"] == "TP1"), None)
        tp2_ts = next((int(f["created_at"]) for f in follow if f["followup_type"] == "TP2"), None)
        closed_ts = next((int(f["created_at"]) for f in follow if f["followup_type"] == "CLOSED"), None)

        if tp1_ts is not None and tp1_ts <= checkpoint_ts:
            reached_tp1 = 1
        if tp2_ts is not None and tp2_ts <= checkpoint_ts:
            reached_tp2 = 1

        if closed_ts is not None and closed_ts <= checkpoint_ts and sl is not None:
            pnl_sl = _pnl_pct(entry, sl, is_long=is_long)
            if max_drawdown_pct is not None and max_drawdown_pct <= pnl_sl + 1e-9:
                stopped = 1

        if closed_ts is not None and checkpoint_ts >= timestamp + 86400:
            if (tp1_ts is None or tp1_ts > checkpoint_ts) and (tp2_ts is None or tp2_ts > checkpoint_ts) and stopped == 0:
                expired = 1

    elif signal_type == "telegram_signal":
        f72 = conn.execute(
            """
            SELECT trade_side, max_profit_pct, max_drawdown_pct, tp1_hit_time, tp2_hit_time,
                   sl_hit_time, exit_reason, holding_seconds, status, exit_time
            FROM market_events_signal_outcomes_f72
            WHERE event_id = ?
            """,
            (signal_id,),
        ).fetchone()
        if not f72:
            return
        is_long = str(f72["trade_side"]).upper() == "LONG"
        max_profit_pct = _safe_float(f72["max_profit_pct"])
        max_drawdown_pct = _safe_float(f72["max_drawdown_pct"])
        tp1_hit = f72["tp1_hit_time"]
        tp2_hit = f72["tp2_hit_time"]
        sl_hit = f72["sl_hit_time"]
        if tp1_hit is not None and int(tp1_hit) <= checkpoint_ts:
            reached_tp1 = 1
        if tp2_hit is not None and int(tp2_hit) <= checkpoint_ts:
            reached_tp2 = 1
        if sl_hit is not None and int(sl_hit) <= checkpoint_ts:
            stopped = 1
        expired = 0

    elif signal_type == "validation_signal":
        # Best-effort: validation signals use underlying g3_signal when possible.
        s = _exec_sql(
            conn,
            """
            SELECT s.source_type, s.source_id, s.direction
            FROM market_validation_records_g4 s
            JOIN market_events_signal_learning_s40_signals ss
              ON ss.signal_id = s.id AND ss.signal_type = 'validation_signal'
            WHERE ss.signal_id = ?
            """,
            (signal_id,),
        ).fetchone()
        if not s:
            # Fallback to stored snapshot only.
            max_profit_pct = None
            max_drawdown_pct = None
        else:
            source_type = str(s["source_type"])
            source_id = int(s["source_id"])
            if source_type == "g3_signal":
                g3 = conn.execute(
                    """
                    SELECT direction, status, max_profit_pct, max_drawdown_pct, closed_at
                    FROM market_live_signals_g3
                    WHERE id = ?
                    """,
                    (source_id,),
                ).fetchone()
                if g3:
                    is_long = str(g3["direction"]).upper() == "LONG"
                    max_profit_pct = _safe_float(g3["max_profit_pct"])
                    max_drawdown_pct = _safe_float(g3["max_drawdown_pct"])
            elif source_type in ("replay_complete", "rejected_candidate"):
                out = _exec_sql(
                    conn,
                    """
                    SELECT o.price_entry, o.best_rr, o.would_hit_tp, o.would_hit_sl,
                           o.price_15m, o.price_1h, o.price_4h, o.price_24h,
                           COALESCE(o.direction, c.direction) AS direction
                    FROM market_candidate_outcomes_g32 o
                    JOIN market_candidate_g31 c ON c.id = o.candidate_id
                    WHERE o.id = ?
                    """,
                    (source_id,),
                ).fetchone()
                if out:
                    entry_ = float(out["price_entry"] or 0)
                    rr = float(out["best_rr"] or 0)
                    direction = str(out["direction"] or "LONG")
                    tp1_calc, sl_calc = _tp_sl_from_g32_rr(entry=entry_, rr=rr, direction=direction)
                    # checkpoint -> choose which price col
                    key_to_col = {"15m": "price_15m", "1h": "price_1h", "4h": "price_4h", "24h": "price_24h"}
                    price_col = key_to_col.get(horizon_key)
                    price_at = out[price_col] if price_col else None
                    if price_at is not None:
                        price_at = float(price_at)
                        is_long = direction.upper() == "LONG"
                        pnl_at = _pnl_pct(entry_, price_at, is_long=is_long)
                        max_profit_pct = round(max(0.0, pnl_at), 3)
                        max_drawdown_pct = round(min(0.0, pnl_at), 3)
                        # reached/invalidation approximations
                        if tp1_calc is not None:
                            pnl_tp1 = _pnl_pct(entry_, float(tp1_calc), is_long=is_long)
                            reached_tp1 = 1 if pnl_at >= pnl_tp1 - 1e-9 else 0
                        if sl_calc is not None:
                            pnl_sl = _pnl_pct(entry_, float(sl_calc), is_long=is_long)
                            stopped = 1 if pnl_at <= pnl_sl + 1e-9 else 0

    execute_with_retry(
        conn,
        """
        INSERT OR IGNORE INTO market_events_signal_learning_s40_checkpoints (
            signal_type, signal_id, horizon_key, checkpoint_ts,
            max_profit_pct, max_drawdown_pct,
            reached_tp1, reached_tp2, stopped, expired, holding_time_seconds, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_type,
            signal_id,
            horizon_key,
            checkpoint_ts,
            max_profit_pct,
            max_drawdown_pct,
            reached_tp1,
            reached_tp2,
            stopped,
            expired,
            max(0, int(time.time()) - timestamp),
            int(time.time()),
        ),
    )


def ingest_checkpoints_s40(conn: Any, *, signal_limit: int = 200) -> int:
    now = int(time.time())
    n = 0

    # Limit the workload to newest signals.
    sig_rows = conn.execute(
        """
        SELECT signal_type, signal_id, timestamp
        FROM market_events_signal_learning_s40_signals
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (signal_limit,),
    ).fetchall()
    for s in sig_rows:
        s_type = str(s["signal_type"])
        sid = int(s["signal_id"])
        ts = int(s["timestamp"] or 0)
        if not ts:
            continue
        if now < ts + 900:
            continue

        for horizon_key, horizon_s in HORIZONS_S40:
            if now < ts + horizon_s:
                continue
            # Insert if missing.
            before = conn.execute(
                """
                SELECT 1
                FROM market_events_signal_learning_s40_checkpoints
                WHERE signal_type = ? AND signal_id = ? AND horizon_key = ?
                """,
                (s_type, sid, horizon_key),
            ).fetchone()
            if before:
                continue
            _ensure_checkpoint_for_signal(
                conn,
                signal_type=s_type,
                signal_id=sid,
                timestamp=ts,
                horizon_key=horizon_key,
                horizon_s=horizon_s,
            )
            n += 1
    return n


def _build_claude_prompt_s40(
    *,
    signal_type: str,
    symbol: str,
    direction: str,
    entry: float | None,
    stop: float | None,
    tp1: float | None,
    tp2: float | None,
    timestamp: int | None,
    snapshot: dict[str, Any],
    outcome: dict[str, Any],
    checkpoint_summary: str,
) -> tuple[str, str]:
    system = "\n".join([
        "Ты рыночный аналитик. Дай только текстовый анализ для обучения.",
        "Никаких изменений логики бота, никаких формул для кода.",
        "Стиль: по делу, по-русски.",
    ])

    user = "\n".join([
        f"Был сигнал {direction} {symbol}.",
        "",
        "Вот рынок:",
        f"- Funding: {snapshot.get('funding')}",
        f"- Open Interest: {snapshot.get('open_interest')}",
        f"- Volume: {snapshot.get('volume')}",
        f"- ATR: {snapshot.get('atr')}",
        f"- Fear & Greed: {snapshot.get('fear_greed')}",
        f"- Trend: {snapshot.get('trend')}",
        f"- News Score / Impact: {snapshot.get('news')}",
        f"- Pattern: {snapshot.get('pattern')}",
        f"- Decision confidence: {snapshot.get('decision_confidence')}",
        "",
        "Сигнал (для ориентира):",
        f"- Entry: {entry}",
        f"- Stop: {stop}",
        f"- TP1: {tp1}",
        f"- TP2: {tp2}",
        f"- Timestamp: {timestamp}",
        "",
        "Вот новости:",
        f"{snapshot.get('news_text') or snapshot.get('news')}",
        "",
        "Вот результат:",
        f"- WIN/LOSS/BE: {outcome.get('win_loss_be')}",
        f"- PnL %: {outcome.get('pnl_pct')}",
        f"- RR achieved: {outcome.get('rr_achieved')}",
        f"- Holding time: {outcome.get('holding_time_seconds')}",
        "",
        "Хронология (достигнутые признаки по горизонтам):",
        checkpoint_summary,
        "",
        "Почему он сработал или не сработал?",
        "Какие признаки были самыми полезными?",
        "Какие признаки были самыми худшими?",
        "",
        "В конце сделай строго два блока:",
        "Лучшие признаки: (список строк в формате ✓ Название, максимум 6)",
        "Худшие признаки: (список строк в формате ✗ Название, максимум 6)",
    ])
    return system, user


def _format_checkpoint_summary_s40(checkpoints: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    by_h = {c["horizon_key"]: c for c in checkpoints}
    for hk, _sec in HORIZONS_S40:
        c = by_h.get(hk)
        if not c:
            continue
        lines.append(
            f"{hk}: max_profit={c.get('max_profit_pct')} max_dd={c.get('max_drawdown_pct')} "
            f"tp1={bool(c.get('reached_tp1'))} tp2={bool(c.get('reached_tp2'))} "
            f"stopped={bool(c.get('stopped'))} expired={bool(c.get('expired'))}"
        )
    return "\n".join(lines) if lines else "нет данных по горизонтам"


def _aggregate_signs_from_analyses(texts: list[str]) -> tuple[list[str], list[str]]:
    best: dict[str, int] = {}
    worst: dict[str, int] = {}
    for t in texts:
        for m in re.finditer(r"^✓\s*(.+)$", t, flags=re.MULTILINE):
            k = m.group(1).strip()
            if k:
                best[k] = best.get(k, 0) + 1
        for m in re.finditer(r"^✗\s*(.+)$", t, flags=re.MULTILINE):
            k = m.group(1).strip()
            if k:
                worst[k] = worst.get(k, 0) + 1
    best_lines = [f"✓ {k} ({v})" for k, v in sorted(best.items(), key=lambda x: x[1], reverse=True)[:8]]
    worst_lines = [f"✗ {k} ({v})" for k, v in sorted(worst.items(), key=lambda x: x[1], reverse=True)[:8]]
    return best_lines, worst_lines


def _fetch_pending_review_pool_s40(
    conn: Any,
    *,
    symbol: str | None,
    pool_limit: int,
) -> list[dict[str, Any]]:
    """Fetch pending closed reviews across lanes."""
    where_symbol = "AND s.symbol = ?" if symbol else ""
    params: list[Any] = [symbol] if symbol else []
    limit = max(20, int(pool_limit))

    g3_sql = f"""
        SELECT 'g3_signal' AS signal_type, s.signal_id, s.symbol, s.direction, s.timestamp,
               s.entry, s.stop, s.tp1, s.tp2,
               g.pnl_pct, g.risk_reward, g.holding_seconds, g.status AS source_status
        FROM market_events_signal_learning_s40_signals s
        JOIN market_live_signals_g3 g ON g.id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'g3_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'g3_signal'
          AND (lower(g.status) = 'closed' OR g.closed_at IS NOT NULL OR g.holding_seconds IS NOT NULL)
          AND (r.signal_id IS NULL OR COALESCE(r.review_status, 'complete') = 'pending_ai')
          {where_symbol}
        ORDER BY s.timestamp DESC
        LIMIT ?
    """
    tel_sql = f"""
        SELECT 'telegram_signal' AS signal_type, s.signal_id, s.symbol, s.direction, s.timestamp,
               s.entry, s.stop, s.tp1, s.tp2,
               f.pnl_pct, f.risk_reward, f.holding_seconds, f.status AS source_status
        FROM market_events_signal_learning_s40_signals s
        JOIN market_events_signal_outcomes_f72 f ON f.event_id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'telegram_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'telegram_signal'
          AND lower(f.status) = 'closed'
          AND (r.signal_id IS NULL OR COALESCE(r.review_status, 'complete') = 'pending_ai')
          {where_symbol}
        ORDER BY s.timestamp DESC
        LIMIT ?
    """
    val_sql = f"""
        SELECT 'validation_signal' AS signal_type, s.signal_id, s.symbol, s.direction, s.timestamp,
               s.entry, s.stop, s.tp1, s.tp2,
               v.pnl_pct, v.rr, NULL AS holding_seconds, 'VALIDATION' AS source_status
        FROM market_events_signal_learning_s40_signals s
        JOIN market_validation_records_g4 v ON v.id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'validation_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'validation_signal'
          AND (r.signal_id IS NULL OR COALESCE(r.review_status, 'complete') = 'pending_ai')
          {where_symbol}
        ORDER BY s.timestamp DESC
        LIMIT ?
    """
    g3 = _exec_sql(conn, g3_sql, (*params, limit)).fetchall()
    tel = _exec_sql(conn, tel_sql, (*params, limit)).fetchall()
    val = _exec_sql(conn, val_sql, (*params, limit)).fetchall()
    combined = [dict(r) for r in list(g3) + list(tel) + list(val)]
    combined.sort(key=lambda r: int(r.get("timestamp") or 0), reverse=True)
    seen: set[tuple[str, int]] = set()
    unique: list[dict[str, Any]] = []
    for r in combined:
        key = (str(r["signal_type"]), int(r["signal_id"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def _pick_newest_and_oldest_s40(
    pool: list[dict[str, Any]],
    *,
    newest_n: int = QUEUE_NEWEST_PER_CYCLE,
    oldest_n: int = QUEUE_OLDEST_PER_CYCLE,
    limit: int = MAX_REVIEWS_PER_CYCLE,
) -> list[dict[str, Any]]:
    """Take up to `newest_n` newest and `oldest_n` oldest pending reviews."""
    if not pool:
        return []
    by_new = sorted(pool, key=lambda r: int(r.get("timestamp") or 0), reverse=True)
    by_old = sorted(pool, key=lambda r: int(r.get("timestamp") or 0))
    picked: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    newest_added = 0
    for r in by_new:
        if newest_added >= newest_n or len(picked) >= limit:
            break
        key = (str(r["signal_type"]), int(r["signal_id"]))
        if key in seen:
            continue
        seen.add(key)
        picked.append(r)
        newest_added += 1

    oldest_added = 0
    for r in by_old:
        if oldest_added >= oldest_n or len(picked) >= limit:
            break
        key = (str(r["signal_type"]), int(r["signal_id"]))
        if key in seen:
            continue
        seen.add(key)
        picked.append(r)
        oldest_added += 1

    return picked[:limit]


def _fetch_review_candidates_s40(
    conn: Any,
    *,
    symbol: str | None,
    limit: int,
    newest_n: int = QUEUE_NEWEST_PER_CYCLE,
    oldest_n: int = QUEUE_OLDEST_PER_CYCLE,
) -> list[dict[str, Any]]:
    """Pick closed pending reviews: newest + oldest mix per cycle."""
    if limit <= 0:
        limit = MAX_REVIEWS_PER_CYCLE
    pool = _fetch_pending_review_pool_s40(conn, symbol=symbol, pool_limit=max(limit * 10, 50))
    return _pick_newest_and_oldest_s40(
        pool, newest_n=newest_n, oldest_n=oldest_n, limit=limit,
    )


def _fetch_signal_snapshot_for_prompt(conn: Any, signal_type: str, signal_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT snapshot_funding, snapshot_open_interest, snapshot_volume, snapshot_atr,
               snapshot_fear_greed, snapshot_trend, snapshot_news_score, snapshot_news_impact,
               snapshot_pattern_json, snapshot_decision_confidence
        FROM market_events_signal_learning_s40_signals
        WHERE signal_type = ? AND signal_id = ?
        """,
        (signal_type, signal_id),
    ).fetchone()
    if not row:
        return {}
    return dict(row)


def _fetch_signal_checkpoints(conn: Any, signal_type: str, signal_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT horizon_key, max_profit_pct, max_drawdown_pct,
               reached_tp1, reached_tp2, stopped, expired
        FROM market_events_signal_learning_s40_checkpoints
        WHERE signal_type = ? AND signal_id = ?
        """,
        (signal_type, signal_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _pattern_label_from_snapshot(snapshot: dict[str, Any]) -> str:
    raw = snapshot.get("snapshot_pattern_json") or snapshot.get("pattern") or ""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            text = str(raw).strip()
            return text[:80] if text else "—"
    if isinstance(data, dict):
        for key in ("pattern", "name", "pattern_name", "label", "candidate_state"):
            val = data.get(key)
            if val:
                return str(val)[:80]
    return "—"


def _news_label_from_snapshot(snapshot: dict[str, Any]) -> str:
    for key in ("snapshot_news_impact", "news", "news_text", "snapshot_news_score"):
        val = snapshot.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()[:80]
    return "—"


def _placeholder_review_text_s40(
    *,
    signal_row: dict[str, Any],
    snapshot: dict[str, Any],
) -> str:
    direction = str(signal_row.get("direction") or "FLAT")
    news = _news_label_from_snapshot(snapshot)
    pattern = _pattern_label_from_snapshot(snapshot)
    return "\n".join([
        "Claude unavailable.",
        "",
        "Market:",
        direction,
        "",
        "News:",
        news,
        "",
        "Pattern:",
        pattern,
        "",
        "Auto review postponed.",
    ])


def _local_review_text_s40(
    *,
    signal_row: dict[str, Any],
    snapshot: dict[str, Any],
    checkpoints: list[dict[str, Any]] | None = None,
) -> str:
    """Deterministic local review when automatic Claude is disabled (S5.0 / BUG-S5.0)."""
    direction = str(signal_row.get("direction") or "FLAT")
    symbol = str(signal_row.get("symbol") or "—")
    pnl_pct = _safe_float(signal_row.get("pnl_pct"))
    win_loss = _win_loss_be(pnl_pct)
    rr = _safe_float(signal_row.get("risk_reward") or signal_row.get("rr"))
    holding = signal_row.get("holding_seconds")
    news = _news_label_from_snapshot(snapshot)
    pattern = _pattern_label_from_snapshot(snapshot)
    conf = snapshot.get("snapshot_decision_confidence")
    trend = snapshot.get("snapshot_trend")
    cp_lines = _format_checkpoint_summary_s40(checkpoints or [])

    hold_h = None
    try:
        if holding is not None:
            hold_h = round(float(holding) / 3600.0, 2)
    except (TypeError, ValueError):
        hold_h = None

    lines = [
        "Local Review (automatic Claude disabled)",
        "",
        f"Symbol: {symbol}",
        f"Direction: {direction}",
        f"Outcome: {win_loss}",
        f"PnL: {pnl_pct if pnl_pct is not None else '—'}%",
        f"RR: {rr if rr is not None else '—'}",
        f"Hold: {hold_h if hold_h is not None else '—'}h",
        "",
        "Market context:",
        f"  Trend: {trend or '—'}",
        f"  News: {news}",
        f"  Pattern: {pattern}",
        f"  Decision confidence: {conf if conf is not None else '—'}",
        "",
        "Horizons:",
        cp_lines,
        "",
        "Note: Claude review available via Telegram /ai or /analyze.",
    ]
    return "\n".join(lines)


@contextmanager
def _s40_claude_http_budget(*, timeout_sec: float = CLAUDE_REVIEW_TIMEOUT_SEC):
    """Tighten Claude HTTP timeout/retries for this worker only (no global client changes)."""
    old_timeout = os.environ.get("ME_G2_TIMEOUT_SEC")
    old_retries = os.environ.get("ME_G2_MAX_RETRIES")
    os.environ["ME_G2_TIMEOUT_SEC"] = str(max(5, int(timeout_sec)))
    os.environ["ME_G2_MAX_RETRIES"] = "0"
    try:
        yield
    finally:
        if old_timeout is None:
            os.environ.pop("ME_G2_TIMEOUT_SEC", None)
        else:
            os.environ["ME_G2_TIMEOUT_SEC"] = old_timeout
        if old_retries is None:
            os.environ.pop("ME_G2_MAX_RETRIES", None)
        else:
            os.environ["ME_G2_MAX_RETRIES"] = old_retries


def _call_claude_review_bounded_s40(
    *,
    system: str,
    user: str,
    timeout_sec: float = CLAUDE_REVIEW_TIMEOUT_SEC,
) -> str:
    """Call Claude with a hard per-review ceiling so the worker never hangs."""

    def _run() -> str:
        with _s40_claude_http_budget(timeout_sec=timeout_sec):
            resp = call_claude_g2(
                system=system,
                user_content=user,
                label="s40_review",
                max_tokens=1200,
            )
            return resp.text.strip()

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_run)
        try:
            return fut.result(timeout=timeout_sec + 2)
        except FuturesTimeout as exc:
            raise TimeoutError(f"claude review timeout after {timeout_sec}s") from exc


def _generate_review_text_s40(
    *,
    signal_type: str,
    signal_row: dict[str, Any],
    snapshot: dict[str, Any],
    checkpoints: list[dict[str, Any]],
    timeout_sec: float = CLAUDE_REVIEW_TIMEOUT_SEC,
) -> tuple[str, dict[str, Any], str, str, bool, str | None]:
    """Return (analysis_text, outcome_fields, review_status, review_type, timed_out, placeholder_reason)."""
    symbol = str(signal_row["symbol"])
    direction = str(signal_row["direction"] or "LONG")
    entry = signal_row.get("entry")
    stop = signal_row.get("stop")
    tp1 = signal_row.get("tp1")
    tp2 = signal_row.get("tp2")
    timestamp = signal_row.get("timestamp")

    pnl_pct = _safe_float(signal_row.get("pnl_pct"))
    rr = _safe_float(signal_row.get("risk_reward") or signal_row.get("rr"))
    holding = signal_row.get("holding_seconds")
    win_loss_be = _win_loss_be(pnl_pct)

    checkpoint_summary = _format_checkpoint_summary_s40(checkpoints)

    snapshot_pattern = snapshot.get("snapshot_pattern_json") or "{}"
    pattern_text = snapshot_pattern if isinstance(snapshot_pattern, str) else json.dumps(snapshot_pattern, ensure_ascii=False)

    snapshot_for_prompt = {
        "funding": snapshot.get("snapshot_funding"),
        "open_interest": snapshot.get("snapshot_open_interest"),
        "volume": snapshot.get("snapshot_volume"),
        "atr": snapshot.get("snapshot_atr"),
        "fear_greed": snapshot.get("snapshot_fear_greed"),
        "trend": snapshot.get("snapshot_trend"),
        "news": snapshot.get("snapshot_news_impact") or snapshot.get("snapshot_news_score"),
        "news_text": snapshot.get("snapshot_news_impact"),
        "pattern": pattern_text[:2000],
        "decision_confidence": snapshot.get("snapshot_decision_confidence"),
    }

    outcome = {
        "win_loss_be": win_loss_be,
        "pnl_pct": pnl_pct,
        "rr_achieved": rr,
        "holding_time_seconds": holding,
    }

    system, user = _build_claude_prompt_s40(
        signal_type=signal_type,
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        timestamp=timestamp,
        snapshot=snapshot_for_prompt,
        outcome=outcome,
        checkpoint_summary=checkpoint_summary,
    )

    # BUG-S5.0: automatic=false / telegram_only must NOT skip reviews.
    # Use a complete local review so Pending drains without Claude.
    claude_ok, block_reason = claude_call_allowed()
    use_claude = bool(is_claude_configured() and claude_ok)
    if not use_claude:
        return (
            _local_review_text_s40(
                signal_row=signal_row, snapshot=snapshot, checkpoints=checkpoints,
            ),
            outcome,
            REVIEW_STATUS_COMPLETE,
            REVIEW_TYPE_LOCAL,
            False,
            None,
        )

    try:
        text = _call_claude_review_bounded_s40(
            system=system, user=user, timeout_sec=timeout_sec,
        )
        return text, outcome, REVIEW_STATUS_COMPLETE, REVIEW_TYPE_CLAUDE, False, None
    except TimeoutError:
        # Do not leave the signal forever pending — fall back to local complete.
        logger.warning("s40 claude timeout — writing local review (%s)", block_reason)
        return (
            _local_review_text_s40(
                signal_row=signal_row, snapshot=snapshot, checkpoints=checkpoints,
            ),
            outcome,
            REVIEW_STATUS_COMPLETE,
            REVIEW_TYPE_LOCAL,
            False,
            PLACEHOLDER_REASON_TIMEOUT,
        )
    except json.JSONDecodeError as exc:
        logger.warning("s40 claude review invalid json: %s", exc)
        return (
            _local_review_text_s40(
                signal_row=signal_row, snapshot=snapshot, checkpoints=checkpoints,
            ),
            outcome,
            REVIEW_STATUS_COMPLETE,
            REVIEW_TYPE_LOCAL,
            False,
            PLACEHOLDER_REASON_INVALID_JSON,
        )
    except (ClaudeClientError, OSError, ConnectionError) as exc:
        logger.warning("s40 claude unavailable for review: %s", exc)
        return (
            _local_review_text_s40(
                signal_row=signal_row, snapshot=snapshot, checkpoints=checkpoints,
            ),
            outcome,
            REVIEW_STATUS_COMPLETE,
            REVIEW_TYPE_LOCAL,
            False,
            PLACEHOLDER_REASON_CLAUDE_UNAVAILABLE,
        )
    except Exception as exc:
        logger.warning("s40 claude review failed: %s", exc)
        return (
            _local_review_text_s40(
                signal_row=signal_row, snapshot=snapshot, checkpoints=checkpoints,
            ),
            outcome,
            REVIEW_STATUS_COMPLETE,
            REVIEW_TYPE_LOCAL,
            False,
            PLACEHOLDER_REASON_OTHER,
        )


def run_learning_pipeline_s40_once(*, limit_ingest: int = 200, limit_checkpoints: int = 200) -> dict[str, Any]:
    """Populate s40_signals + s40_checkpoints (no Claude)."""
    with market_events_connection() as conn:
        n_ingest = ingest_new_s40_signals(conn, limit=limit_ingest)
        n_cp = ingest_checkpoints_s40(conn, signal_limit=limit_checkpoints)
        conn.commit()
    return {"ingested": n_ingest, "checkpoints_written": n_cp}


def _persist_reviews_s40(
    rows: list[tuple[str, int, str, dict[str, Any], str, str, str | None]],
) -> int:
    if not rows:
        return 0
    written = 0
    with market_events_connection() as conn:
        has_reason_col = True
        try:
            cols = {
                str(r[1])
                for r in conn.execute(
                    "PRAGMA table_info(market_events_signal_learning_s40_reviews)",
                ).fetchall()
            }
            has_reason_col = "placeholder_reason" in cols
        except Exception:
            has_reason_col = False

        for st, sid, analysis_text, outcome_fields, review_status, review_type, placeholder_reason in rows:
            now = int(time.time())
            if has_reason_col:
                execute_with_retry(
                    conn,
                    """
                    INSERT OR REPLACE INTO market_events_signal_learning_s40_reviews (
                        signal_type, signal_id, reviewed_at, analysis_text,
                        win_loss_be, pnl_pct, rr_achieved, created_at, updated_at,
                        review_status, review_type, placeholder_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        st,
                        sid,
                        now,
                        analysis_text,
                        outcome_fields.get("win_loss_be"),
                        outcome_fields.get("pnl_pct"),
                        outcome_fields.get("rr_achieved"),
                        now,
                        now,
                        review_status,
                        review_type,
                        placeholder_reason,
                    ),
                )
            else:
                execute_with_retry(
                    conn,
                    """
                    INSERT OR REPLACE INTO market_events_signal_learning_s40_reviews (
                        signal_type, signal_id, reviewed_at, analysis_text,
                        win_loss_be, pnl_pct, rr_achieved, created_at, updated_at,
                        review_status, review_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        st,
                        sid,
                        now,
                        analysis_text,
                        outcome_fields.get("win_loss_be"),
                        outcome_fields.get("pnl_pct"),
                        outcome_fields.get("rr_achieved"),
                        now,
                        now,
                        review_status,
                        review_type,
                    ),
                )
            written += 1
        conn.commit()
    return written


def _count_pending_review_queue_s40(conn: Any) -> int:
    """Closed signals still waiting for a complete Claude review."""
    g3 = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_events_signal_learning_s40_signals s
        JOIN market_live_signals_g3 g ON g.id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'g3_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'g3_signal'
          AND (lower(g.status) = 'closed' OR g.closed_at IS NOT NULL OR g.holding_seconds IS NOT NULL)
          AND (r.signal_id IS NULL OR COALESCE(r.review_status, 'complete') = 'pending_ai')
        """,
    ).fetchone()["n"]
    tel = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_events_signal_learning_s40_signals s
        JOIN market_events_signal_outcomes_f72 f ON f.event_id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'telegram_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'telegram_signal'
          AND lower(f.status) = 'closed'
          AND (r.signal_id IS NULL OR COALESCE(r.review_status, 'complete') = 'pending_ai')
        """,
    ).fetchone()["n"]
    val = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_events_signal_learning_s40_signals s
        JOIN market_validation_records_g4 v ON v.id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'validation_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'validation_signal'
          AND (r.signal_id IS NULL OR COALESCE(r.review_status, 'complete') = 'pending_ai')
        """,
    ).fetchone()["n"]
    return int(g3 or 0) + int(tel or 0) + int(val or 0)


def format_learning_cycle_report_s40(stats: dict[str, Any]) -> str:
    return "\n".join([
        "Cycle",
        "",
        f"Mode: {stats.get('mode') or '—'}",
        f"Signals ingested: {int(stats.get('ingested') or 0)}",
        f"Checkpoints: {int(stats.get('checkpoints_written') or 0)}",
        "",
        f"Reviews completed: {int(stats.get('reviews_completed') or 0)}",
        f"Local reviews: {int(stats.get('local_reviews_written') or 0)}",
        f"Skipped (timeout): {int(stats.get('skipped_timeout') or 0)}",
        f"Remaining queue: {int(stats.get('remaining_queue') or 0)}",
        "",
        f"Duration: {int(stats.get('duration_sec') or 0)} sec",
    ])


def run_learning_reviews_s40_once(
    *,
    limit: int = MAX_REVIEWS_PER_CYCLE,
    timeout_sec: float = CLAUDE_REVIEW_TIMEOUT_SEC,
    newest_n: int = QUEUE_NEWEST_PER_CYCLE,
    oldest_n: int = QUEUE_OLDEST_PER_CYCLE,
) -> dict[str, Any]:
    """Background-only review generation. Report commands must stay readonly.

    Throughput: at most `limit` reviews per call (2 newest + 3 oldest by default).
    When automatic Claude is disabled (S5.0), uses Local Review and a higher
    per-cycle budget so Missing review drains without API calls.
    """
    claude_ok, _ = claude_call_allowed()
    use_claude = bool(is_claude_configured() and claude_ok)
    if limit <= 0:
        limit = MAX_REVIEWS_PER_CYCLE if use_claude else MAX_LOCAL_REVIEWS_PER_CYCLE
    elif not use_claude and limit < MAX_LOCAL_REVIEWS_PER_CYCLE:
        # BUG-S5.0.1: drain missing reviews with local analyzer, not 5/cycle.
        limit = MAX_LOCAL_REVIEWS_PER_CYCLE
        newest_n = max(newest_n, limit // 2)
        oldest_n = max(oldest_n, limit - newest_n)

    with market_events_readonly_connection() as ro:
        remaining_before = _count_pending_review_queue_s40(ro)
        candidates = _fetch_review_candidates_s40(
            ro, symbol=None, limit=limit, newest_n=newest_n, oldest_n=oldest_n,
        )
        enriched: list[dict[str, Any]] = []
        for c in candidates:
            signal_type = str(c["signal_type"])
            signal_id = int(c["signal_id"])
            snap = _fetch_signal_snapshot_for_prompt(ro, signal_type, signal_id)
            cps = _fetch_signal_checkpoints(ro, signal_type, signal_id)
            enriched.append({
                **dict(c),
                "_snapshot": snap,
                "_checkpoints": cps,
            })

    reviews: list[tuple[str, int, str, dict[str, Any], str, str, str | None]] = []
    skipped_timeout = 0
    placeholders = 0
    completed = 0
    local_n = 0
    item_errors = 0
    review_secs: list[float] = []

    for row in enriched:
        st = str(row["signal_type"])
        sid = int(row["signal_id"])
        t0 = time.perf_counter()
        try:
            analysis_text, outcome_fields, review_status, review_type, timed_out, placeholder_reason = (
                _generate_review_text_s40(
                    signal_type=st,
                    signal_row=row,
                    snapshot=row.get("_snapshot") or {},
                    checkpoints=row.get("_checkpoints") or [],
                    timeout_sec=timeout_sec,
                )
            )
        except Exception:
            item_errors += 1
            logger.exception("s40 review failed for %s/%s — writing local fallback", st, sid)
            try:
                analysis_text = _local_review_text_s40(
                    signal_row=row,
                    snapshot=row.get("_snapshot") or {},
                    checkpoints=row.get("_checkpoints") or [],
                )
                outcome_fields = {
                    "win_loss_be": _win_loss_be(_safe_float(row.get("pnl_pct"))),
                    "pnl_pct": _safe_float(row.get("pnl_pct")),
                    "rr_achieved": _safe_float(row.get("risk_reward") or row.get("rr")),
                    "holding_time_seconds": row.get("holding_seconds"),
                }
                review_status = REVIEW_STATUS_COMPLETE
                review_type = REVIEW_TYPE_LOCAL
                timed_out = False
                placeholder_reason = PLACEHOLDER_REASON_OTHER
            except Exception:
                logger.exception("s40 local fallback also failed for %s/%s", st, sid)
                continue
        review_secs.append(time.perf_counter() - t0)
        if timed_out:
            skipped_timeout += 1
            continue
        if review_type == REVIEW_TYPE_PLACEHOLDER:
            placeholders += 1
        else:
            completed += 1
            if review_type == REVIEW_TYPE_LOCAL:
                local_n += 1
        reviews.append((
            st, sid, analysis_text, outcome_fields, review_status, review_type, placeholder_reason,
        ))

    written = _persist_reviews_s40(reviews)
    if skipped_timeout:
        with market_events_connection() as conn:
            _incr_ops_counter(conn, "total_skipped_timeout", skipped_timeout)
            conn.commit()
    if item_errors:
        with market_events_connection() as conn:
            _incr_ops_counter(conn, "total_worker_errors", item_errors)
            conn.commit()
    avg_review_sec = round(sum(review_secs) / len(review_secs), 1) if review_secs else 0.0

    with market_events_readonly_connection() as ro:
        remaining_after = _count_pending_review_queue_s40(ro)

    return {
        "review_candidates": len(enriched),
        "reviews_written": written,
        "reviews_completed": completed,
        "local_reviews_written": local_n,
        "placeholders_written": placeholders,
        "skipped_timeout": skipped_timeout,
        "item_errors": item_errors,
        "remaining_queue": remaining_after,
        "remaining_queue_before": remaining_before,
        "avg_review_sec": avg_review_sec,
        "mode": "claude" if use_claude else "local",
    }


def _persist_worker_cycle_ops_s40(
    *,
    duration_sec: int,
    avg_review_sec: float | None,
    remaining_queue: int,
) -> None:
    now = int(time.time())
    with market_events_connection() as conn:
        _set_ops_state(conn, "last_cycle_ended_at", str(now))
        _set_ops_state(conn, "last_cycle_duration_sec", str(duration_sec))
        _set_ops_state(conn, "last_remaining_queue", str(remaining_queue))
        if avg_review_sec is not None and avg_review_sec > 0:
            prev = _get_ops_state(conn, "avg_review_sec")
            try:
                prev_f = float(prev) if prev else avg_review_sec
            except (TypeError, ValueError):
                prev_f = avg_review_sec
            # EMA toward latest cycle average
            ema = round(0.7 * prev_f + 0.3 * float(avg_review_sec), 2)
            _set_ops_state(conn, "avg_review_sec", str(ema))
        conn.commit()


def _relative_age(ts: int | None, *, now: int | None = None) -> str:
    if not ts:
        return "—"
    now = now or int(time.time())
    delta = max(0, now - int(ts))
    if delta < 60:
        return f"{delta} sec ago"
    if delta < 3600:
        return f"{delta // 60} min ago"
    if delta < 86400:
        return f"{delta // 3600} h ago"
    return f"{delta // 86400} d ago"


def _format_eta_minutes(remaining: int, *, reviews_per_cycle: int = MAX_REVIEWS_PER_CYCLE, interval_sec: int = 60) -> str:
    if remaining <= 0:
        return "0 min"
    cycles_needed = (remaining + max(1, reviews_per_cycle) - 1) // max(1, reviews_per_cycle)
    minutes = max(1, int(round(cycles_needed * interval_sec / 60.0)))
    return f"{minutes} min"


def _daily_placeholder_review_s43(stats: dict[str, Any]) -> str:
    return "\n".join([
        "Claude unavailable.",
        "",
        f"Signals: {stats.get('signals', 0)}",
        f"Win rate: {stats.get('win_rate', 0)}%",
        f"PnL: {stats.get('pnl_usd', 0):+.2f}",
        f"Best: {stats.get('best_symbol') or '—'}",
        f"Worst: {stats.get('worst_symbol') or '—'}",
        "",
        "Auto daily review postponed.",
    ])


def _daily_local_review_s43(stats: dict[str, Any]) -> str:
    return "\n".join([
        "Local Daily Review (automatic Claude disabled)",
        "",
        f"Signals: {stats.get('signals', 0)}",
        f"Wins: {stats.get('wins', 0)}  Losses: {stats.get('losses', 0)}",
        f"Win rate: {stats.get('win_rate', 0)}%",
        f"PnL: {stats.get('pnl_usd', 0):+.2f}",
        f"Best: {stats.get('best_symbol') or '—'}",
        f"Worst: {stats.get('worst_symbol') or '—'}",
        f"Best pattern: {stats.get('best_pattern') or '—'}",
        f"Worst pattern: {stats.get('worst_pattern') or '—'}",
        f"Avg hold: {stats.get('avg_hold_hours', 0)}h",
        f"Avg RR: {stats.get('avg_rr', 0)}",
        "",
        "Use Telegram /ai for Claude deep-dive.",
    ])


def _generate_daily_claude_review_s43(stats: dict[str, Any]) -> tuple[str, str]:
    """Return (text, review_status). Never raises."""
    claude_ok, _ = claude_call_allowed()
    if not is_claude_configured() or not claude_ok:
        return _daily_local_review_s43(stats), REVIEW_STATUS_COMPLETE

    system = (
        "You are a concise trading research coach. Write at most 250 words. "
        "No code changes. English is fine."
    )
    user = "\n".join([
        "Write a short daily performance review for the paper learning pipeline.",
        "Cover: strongest setups, weakest decisions, why losses happened, recommendation for tomorrow.",
        "",
        f"Signals: {stats.get('signals')}",
        f"Wins: {stats.get('wins')}  Losses: {stats.get('losses')}",
        f"Win rate: {stats.get('win_rate')}%",
        f"PnL $: {stats.get('pnl_usd')}",
        f"Best symbol: {stats.get('best_symbol')}",
        f"Worst symbol: {stats.get('worst_symbol')}",
        f"Best pattern: {stats.get('best_pattern')}",
        f"Worst pattern: {stats.get('worst_pattern')}",
        f"Avg hold hours: {stats.get('avg_hold_hours')}",
        f"Avg RR: {stats.get('avg_rr')}",
    ])
    try:
        text = _call_claude_review_bounded_s40(system=system, user=user, timeout_sec=CLAUDE_REVIEW_TIMEOUT_SEC)
        words = text.split()
        if len(words) > 250:
            text = " ".join(words[:250])
        return text, REVIEW_STATUS_COMPLETE
    except Exception as exc:
        logger.warning("s43 daily claude review unavailable: %s", exc)
        return _daily_local_review_s43(stats), REVIEW_STATUS_COMPLETE


def maybe_emit_daily_performance_s43() -> dict[str, Any]:
    """At local 22:00 create daily paper performance row + optional Claude review (DB only)."""
    now = int(time.time())
    from datetime import datetime
    dt = datetime.fromtimestamp(now)
    day_key = dt.strftime("%Y-%m-%d")
    result: dict[str, Any] = {"emitted": False, "day_key": day_key}

    if dt.hour < DAILY_PERFORMANCE_HOUR:
        return result

    with market_events_connection() as conn:
        if _get_ops_state(conn, "last_daily_performance_s43") == day_key:
            conn.commit()
            return result

        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            paper_day_stats_s42,
        )
        stats = paper_day_stats_s42(conn)
        conn.commit()

    review_text, review_status = _generate_daily_claude_review_s43(stats)

    with market_events_connection() as conn:
        execute_with_retry(
            conn,
            """
            INSERT OR REPLACE INTO market_events_learning_daily_s43 (
              day_key, signals, wins, losses, win_rate, pnl_usd,
              best_symbol, worst_symbol, best_pattern, worst_pattern,
              avg_hold_hours, avg_rr, claude_review_text, review_status,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day_key,
                int(stats.get("signals") or 0),
                int(stats.get("wins") or 0),
                int(stats.get("losses") or 0),
                float(stats.get("win_rate") or 0),
                float(stats.get("pnl_usd") or 0),
                stats.get("best_symbol"),
                stats.get("worst_symbol"),
                stats.get("best_pattern"),
                stats.get("worst_pattern"),
                float(stats.get("avg_hold_hours") or 0),
                float(stats.get("avg_rr") or 0),
                review_text,
                review_status,
                now,
                now,
            ),
        )
        _set_ops_state(conn, "last_daily_performance_s43", day_key)
        conn.commit()

    result["emitted"] = True
    result["stats"] = stats
    result["review_status"] = review_status
    return result


def run_learning_worker_s40(
    *,
    interval_sec: int = 60,
    max_cycles: int | None = None,
    max_reviews_per_cycle: int = MAX_REVIEWS_PER_CYCLE,
    claude_timeout_sec: float = CLAUDE_REVIEW_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Minute worker: ingest -> checkpoints -> bounded reviews -> paper -> daily.

    Heavy Claude/HTTP work always happens outside write connections.
    """
    cycles = 0
    ingested = 0
    checkpoints = 0
    reviews = 0
    reviews_completed = 0
    skipped_timeout = 0
    paper_opened = 0
    paper_ticked = 0
    errors = 0
    last_cycle: dict[str, Any] = {}

    with market_events_connection() as conn:
        apply_migrations(conn)
        _ensure_ops_state(conn)

    while True:
        cycles += 1
        t0 = time.perf_counter()
        try:
            prep = run_learning_pipeline_s40_once()
            ingested += int(prep.get("ingested") or 0)
            checkpoints += int(prep.get("checkpoints_written") or 0)
            rev = run_learning_reviews_s40_once(
                limit=max_reviews_per_cycle,
                timeout_sec=claude_timeout_sec,
            )
            reviews += int(rev.get("reviews_written") or 0)
            reviews_completed += int(rev.get("reviews_completed") or 0)
            skipped_timeout += int(rev.get("skipped_timeout") or 0)
            from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
                run_paper_performance_cycle_s42,
            )
            paper = run_paper_performance_cycle_s42()
            paper_opened += int(paper.get("opened") or 0)
            paper_ticked += int(paper.get("ticked") or 0)
            try:
                maybe_emit_daily_performance_s43()
            except Exception:
                logger.exception("s43 daily performance failed")
            duration_sec = int(round(time.perf_counter() - t0))
            last_cycle = {
                "ingested": int(prep.get("ingested") or 0),
                "checkpoints_written": int(prep.get("checkpoints_written") or 0),
                "reviews_completed": int(rev.get("reviews_completed") or 0) + int(rev.get("placeholders_written") or 0),
                "local_reviews_written": int(rev.get("local_reviews_written") or 0),
                "skipped_timeout": int(rev.get("skipped_timeout") or 0),
                "remaining_queue": int(rev.get("remaining_queue") or 0),
                "duration_sec": duration_sec,
                "mode": rev.get("mode") or "—",
            }
            _persist_worker_cycle_ops_s40(
                duration_sec=duration_sec,
                avg_review_sec=float(rev.get("avg_review_sec") or 0) or None,
                remaining_queue=int(rev.get("remaining_queue") or 0),
            )
            print(format_learning_cycle_report_s40(last_cycle), flush=True)
        except Exception:
            errors += 1
            logger.exception("s40 worker cycle failed")
            try:
                with market_events_connection() as conn:
                    _incr_ops_counter(conn, "total_worker_errors", 1)
                    conn.commit()
            except Exception:
                logger.exception("s40 worker error counter failed")

        if max_cycles is not None and cycles >= max_cycles:
            break
        time.sleep(max(5, int(interval_sec)))

    return {
        "cycles": cycles,
        "ingested": ingested,
        "checkpoints_written": checkpoints,
        "reviews_written": reviews,
        "reviews_completed": reviews_completed,
        "skipped_timeout": skipped_timeout,
        "paper_opened": paper_opened,
        "paper_ticked": paper_ticked,
        "errors": errors,
        "last_cycle": last_cycle,
    }


def learning_status_s40() -> str:
    now = int(time.time())
    with market_events_readonly_connection() as conn:
        sig_n = conn.execute(
            "SELECT COUNT(*) AS n FROM market_events_signal_learning_s40_signals",
        ).fetchone()["n"]
        rev_complete = conn.execute(
            """
            SELECT COUNT(*) AS n FROM market_events_signal_learning_s40_reviews
            WHERE COALESCE(review_status, 'complete') = 'complete'
            """,
        ).fetchone()["n"]
        try:
            placeholder_n = conn.execute(
                """
                SELECT COUNT(*) AS n FROM market_events_signal_learning_s40_reviews
                WHERE COALESCE(review_type, 'claude') = 'placeholder'
                   OR COALESCE(review_status, 'complete') = 'pending_ai'
                """,
            ).fetchone()["n"]
            pending = _count_pending_review_queue_s40(conn)
            last_rev = conn.execute(
                "SELECT MAX(reviewed_at) AS t FROM market_events_signal_learning_s40_reviews",
            ).fetchone()["t"]
            avg_review = _get_ops_state(conn, "avg_review_sec")
            last_cycle_at = _get_ops_state(conn, "last_cycle_ended_at")
            last_cycle_dur = _get_ops_state(conn, "last_cycle_duration_sec")
        except Exception:
            placeholder_n = 0
            pending = max(0, sig_n - rev_complete)
            last_rev = None
            avg_review = None
            last_cycle_at = None
            last_cycle_dur = None

        try:
            from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
                paper_performance_dashboard_s42,
            )
            paper = paper_performance_dashboard_s42(conn)
            paper_trades = int(paper.get("trades") or 0)
            paper_pnl = float(paper.get("current_equity") or 100) - 100.0
            # Prefer total closed pnl if available via equity delta; also show weekly as fallback
            paper_pnl = float(paper.get("today_pnl_usd") or 0)  # will replace below
        except Exception:
            paper_trades = 0
            paper_pnl = 0.0
            paper = {}

        try:
            pnl_row = conn.execute(
                """
                SELECT COALESCE(SUM(pnl_usd), 0) AS s
                FROM market_events_paper_trades_s42
                WHERE status = 'CLOSED'
                """,
            ).fetchone()
            paper_pnl = float(pnl_row["s"] or 0)
            paper_trades = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42",
            ).fetchone()["n"]
        except Exception:
            pass

    try:
        avg_f = float(avg_review) if avg_review else 0.0
    except (TypeError, ValueError):
        avg_f = 0.0

    try:
        last_cycle_ts = int(last_cycle_at) if last_cycle_at else None
    except (TypeError, ValueError):
        last_cycle_ts = None

    if last_cycle_ts and (now - last_cycle_ts) <= WORKER_RUNNING_WINDOW_SEC:
        worker_status = "RUNNING"
    elif last_cycle_ts:
        worker_status = "IDLE"
    else:
        worker_status = "UNKNOWN"

    eta = _format_eta_minutes(int(pending or 0))
    pnl_sign = "+" if paper_pnl >= 0 else ""
    last_cycle_line = _relative_age(last_cycle_ts, now=now)
    if last_cycle_dur:
        last_cycle_line = f"{last_cycle_line} ({last_cycle_dur} sec)"

    return "\n".join([
        "Learning Pipeline",
        "",
        "Signals:",
        str(int(sig_n or 0)),
        "",
        "Reviews:",
        str(int(rev_complete or 0)),
        "",
        "Pending:",
        str(int(pending or 0)),
        "",
        "Placeholder:",
        str(int(placeholder_n or 0)),
        "",
        "Last review:",
        _relative_age(int(last_rev) if last_rev else None, now=now),
        "",
        "Average review:",
        f"{avg_f:.1f} sec" if avg_f else "—",
        "",
        "Queue ETA:",
        eta,
        "",
        "Paper trades:",
        str(int(paper_trades or 0)),
        "",
        "Paper PnL:",
        f"{pnl_sign}${paper_pnl:.0f}" if abs(paper_pnl) >= 1 else f"{pnl_sign}${paper_pnl:.2f}",
        "",
        "Worker:",
        worker_status,
        "",
        "Last cycle:",
        last_cycle_line,
    ])


def _count_closed_signals_missing_review_s40(conn: Any) -> int:
    """Closed source signals with no review row at all."""
    g3 = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_events_signal_learning_s40_signals s
        JOIN market_live_signals_g3 g ON g.id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'g3_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'g3_signal'
          AND (lower(g.status) = 'closed' OR g.closed_at IS NOT NULL OR g.holding_seconds IS NOT NULL)
          AND r.signal_id IS NULL
        """,
    ).fetchone()["n"]
    tel = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_events_signal_learning_s40_signals s
        JOIN market_events_signal_outcomes_f72 f ON f.event_id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'telegram_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'telegram_signal'
          AND lower(f.status) = 'closed'
          AND r.signal_id IS NULL
        """,
    ).fetchone()["n"]
    val = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_events_signal_learning_s40_signals s
        JOIN market_validation_records_g4 v ON v.id = s.signal_id
        LEFT JOIN market_events_signal_learning_s40_reviews r
          ON r.signal_type = 'validation_signal' AND r.signal_id = s.signal_id
        WHERE s.signal_type = 'validation_signal'
          AND r.signal_id IS NULL
        """,
    ).fetchone()["n"]
    return int(g3 or 0) + int(tel or 0) + int(val or 0)


def learning_review_analytics_s40(conn: Any) -> dict[str, Any]:
    """Aggregate review source counts and placeholder reason breakdown."""
    claude_n = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_learning_s40_reviews
        WHERE COALESCE(review_type, 'claude') = ?
          AND COALESCE(review_status, 'complete') = ?
        """,
        (REVIEW_TYPE_CLAUDE, REVIEW_STATUS_COMPLETE),
    ).fetchone()["n"]
    local_n = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_learning_s40_reviews
        WHERE COALESCE(review_type, 'claude') = ?
        """,
        (REVIEW_TYPE_LOCAL,),
    ).fetchone()["n"]
    placeholder_n = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_learning_s40_reviews
        WHERE COALESCE(review_type, 'claude') = ?
        """,
        (REVIEW_TYPE_PLACEHOLDER,),
    ).fetchone()["n"]

    reason_counts = {
        PLACEHOLDER_REASON_CLAUDE_UNAVAILABLE: 0,
        PLACEHOLDER_REASON_TIMEOUT: 0,
        PLACEHOLDER_REASON_INVALID_JSON: 0,
        PLACEHOLDER_REASON_OTHER: 0,
    }
    has_reason_col = False
    try:
        cols = {
            str(r[1])
            for r in conn.execute(
                "PRAGMA table_info(market_events_signal_learning_s40_reviews)",
            ).fetchall()
        }
        has_reason_col = "placeholder_reason" in cols
    except Exception:
        has_reason_col = False

    if has_reason_col:
        rows = conn.execute(
            """
            SELECT analysis_text, placeholder_reason
            FROM market_events_signal_learning_s40_reviews
            WHERE COALESCE(review_type, 'claude') = ?
            """,
            (REVIEW_TYPE_PLACEHOLDER,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT analysis_text, NULL AS placeholder_reason
            FROM market_events_signal_learning_s40_reviews
            WHERE COALESCE(review_type, 'claude') = ?
            """,
            (REVIEW_TYPE_PLACEHOLDER,),
        ).fetchall()

    for row in rows:
        reason = _infer_placeholder_reason(
            row["analysis_text"],
            stored_reason=row["placeholder_reason"] if has_reason_col else None,
        )
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    skipped_timeout_ops = 0
    worker_errors_ops = 0
    try:
        skipped_timeout_ops = int(_get_ops_state(conn, "total_skipped_timeout") or 0)
    except (TypeError, ValueError):
        skipped_timeout_ops = 0
    try:
        worker_errors_ops = int(_get_ops_state(conn, "total_worker_errors") or 0)
    except (TypeError, ValueError):
        worker_errors_ops = 0

    reason_counts[PLACEHOLDER_REASON_TIMEOUT] += skipped_timeout_ops

    missing_review = _count_closed_signals_missing_review_s40(conn)

    return {
        "claude_reviews": int(claude_n or 0),
        "local_reviews": int(local_n or 0),
        "placeholder_reviews": int(placeholder_n or 0),
        "errors": int(missing_review or 0) + worker_errors_ops,
        "missing_review": int(missing_review or 0),
        "worker_errors": worker_errors_ops,
        "skipped_timeout_ops": skipped_timeout_ops,
        "placeholder_reasons": reason_counts,
    }


def learning_health_s40() -> str:
    """FIX-S4.4A — validate accumulated learning + paper analytics."""
    with market_events_readonly_connection() as conn:
        analytics = learning_review_analytics_s40(conn)
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            paper_performance_dashboard_s42,
            paper_trade_counts_s42,
        )
        paper = paper_performance_dashboard_s42(conn)
        counts = paper_trade_counts_s42(conn)

        closed_rows = conn.execute(
            """
            SELECT pnl_usd, closed_at, created_at
            FROM market_events_paper_trades_s42
            WHERE status = 'CLOSED' AND closed_at IS NOT NULL
            ORDER BY closed_at ASC
            """,
        ).fetchall()
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            _max_drawdown_pct_from_pnl_usd,
        )
        pnls = [float(r["pnl_usd"] or 0) for r in closed_rows]
        max_dd = _max_drawdown_pct_from_pnl_usd(pnls)

    reasons = analytics.get("placeholder_reasons") or {}
    lines = [
        "Learning Health (S4.4A)",
        "",
        "Reviews by source:",
        f"  Claude:      {analytics['claude_reviews']}",
        f"  Local:       {analytics['local_reviews']}",
        f"  Placeholder: {analytics['placeholder_reviews']}",
        f"  Errors:      {analytics['errors']}",
        "",
        "Placeholder reasons:",
        f"  Claude unavailable: {reasons.get(PLACEHOLDER_REASON_CLAUDE_UNAVAILABLE, 0)}",
        f"  Timeout:            {reasons.get(PLACEHOLDER_REASON_TIMEOUT, 0)}",
        f"  Invalid JSON:       {reasons.get(PLACEHOLDER_REASON_INVALID_JSON, 0)}",
        f"  Other fallback:     {reasons.get(PLACEHOLDER_REASON_OTHER, 0)}",
        "",
        "Paper trades:",
        f"  Open:               {counts['open_trades']}",
        f"  Closed:             {counts['closed_trades']}",
        f"  Pending Settlement: {counts['pending_settlement']}",
        f"  Breakeven:          {counts['breakeven_trades']}",
        "",
        "Paper account:",
        f"  Equity:   ${paper.get('current_equity', 0):.2f}",
        f"  Max DD:   {max_dd:.1f}%",
    ]
    if analytics.get("missing_review"):
        lines.extend([
            "",
            f"Missing review (closed, no row): {analytics['missing_review']}",
        ])
    if analytics.get("worker_errors"):
        lines.append(f"Worker cycle errors (ops): {analytics['worker_errors']}")
    return "\n".join(lines)


def run_review_s40_cli(*, symbol: str | None = None, last: int = 20) -> str:
    symbol = symbol.upper().replace("USDT", "") if symbol else None
    if last <= 0:
        last = 20
    with market_events_readonly_connection() as conn:
        where_symbol = "AND s.symbol = ?" if symbol else ""
        params: list[Any] = [symbol] if symbol else []
        rows = conn.execute(
            f"""
            SELECT s.symbol, s.direction, r.win_loss_be, r.analysis_text
            FROM market_events_signal_learning_s40_reviews r
            JOIN market_events_signal_learning_s40_signals s
              ON s.signal_type = r.signal_type AND s.signal_id = r.signal_id
            WHERE 1=1
            {where_symbol}
            ORDER BY s.timestamp DESC
            LIMIT ?
            """,
            (*params, last),
        ).fetchall()

        win = sum(1 for rr in rows if str(rr["win_loss_be"]) == "WIN")
        loss = sum(1 for rr in rows if str(rr["win_loss_be"]) == "LOSS")
        be = sum(1 for rr in rows if str(rr["win_loss_be"]) == "BE")

        analyses = [str(rr["analysis_text"] or "") for rr in rows if rr["analysis_text"]]
        best_lines, worst_lines = _aggregate_signs_from_analyses(analyses)

        total = len(rows)
        header = f"{total} signals (last {last}) — WIN {win} LOSS {loss} BE {be}"
        out = [header, ""]
        if best_lines:
            out.append("Лучшие признаки")
            out.extend(best_lines)
            out.append("")
        if worst_lines:
            out.append("Худшие признаки")
            out.extend(worst_lines)
        if not analyses:
            out.append("Нет stored reviews yet (Claude may be not configured).")
        return "\n".join(out)


__all__ = [
    "MAX_REVIEWS_PER_CYCLE",
    "format_learning_cycle_report_s40",
    "learning_health_s40",
    "learning_review_analytics_s40",
    "learning_status_s40",
    "maybe_emit_daily_performance_s43",
    "run_review_s40_cli",
    "run_learning_pipeline_s40_once",
    "run_learning_reviews_s40_once",
    "run_learning_worker_s40",
]

