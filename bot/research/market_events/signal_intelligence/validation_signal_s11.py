"""Phase S1.1 — Validation Signal pipeline (guaranteed end-to-end test lane).

Isolated from Production, Optimizer, Weight Learning, and Shadow learning datasets.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candidate_g31 import CandidateG31
from bot.research.market_events.signal_intelligence.experimental_g39 import (
    PRODUCTION_PROFILE,
    threshold_failures_g39,
)
from bot.research.market_events.signal_intelligence.shadow_g40 import (
    _current_price,
    _pnl_pct,
    _resolve_price,
    compute_grade_g40,
    passes_shadow_g40,
)
from bot.research.market_events.signal_intelligence.trade_geometry_s12 import (
    assert_sendable_geometry_s12,
    shock_direction_for_trade_side,
)
from bot.research.market_events.signal_intelligence.trade_plan_f71 import compute_trade_plan_f71

logger = logging.getLogger(__name__)

_SIGNALS = "market_validation_signals"
_HORIZONS = "market_validation_horizons"

STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"
SIGNAL_TYPE = "VALIDATION_SIGNAL"

VALIDATION_HEADER = "🧪 VALIDATION SIGNAL"
VALIDATION_RESULT_HEADER = "VALIDATION RESULT"

# User-requested horizons only
VALIDATION_HORIZON_SECS: tuple[tuple[str, int], ...] = (
    ("15m", 900),
    ("1h", 3600),
    ("4h", 14400),
    ("24h", 86400),
)

MAX_PER_DAY = 1


@dataclass(frozen=True)
class ValidationSignalS11:
    signal_id: int
    signal_uuid: str
    symbol: str
    telegram_rendered: str


def pick_validation_candidate_s11(candidates: list[CandidateG31]) -> CandidateG31 | None:
    """Best candidate: conf → market_score → RR.

    Prefer shadow-threshold passers; if none, take best of the cycle anyway.
    """
    if not candidates:
        return None

    def _sort_key(c: CandidateG31) -> tuple[float, float, float]:
        return (float(c.confidence or 0), float(c.market_score or 0), float(c.rr or 0))

    shadow_ok = [c for c in candidates if passes_shadow_g40(c)]
    pool = shadow_ok if shadow_ok else [
        c for c in candidates
        if c.direction and c.confidence is not None and c.market_score is not None
    ]
    if not pool:
        pool = list(candidates)
    pool.sort(key=_sort_key, reverse=True)
    return pool[0]


def _validation_count_today(conn: Any) -> int:
    ts = int(time.time())
    day_start = ts - (ts % 86400)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_SIGNALS} WHERE created_at >= ?",
        (day_start,),
    ).fetchone()
    return int(row["n"] or 0) if row else 0


def _open_validation_symbols(conn: Any) -> set[str]:
    rows = conn.execute(
        f"SELECT symbol FROM {_SIGNALS} WHERE status = ?",
        (STATUS_OPEN,),
    ).fetchall()
    return {str(r["symbol"]) for r in rows}


def format_production_rejection_block_s11(candidate: CandidateG31) -> list[str]:
    failures = threshold_failures_g39(candidate, PRODUCTION_PROFILE)
    if not failures:
        return ["Production rejected because:", "", "(none — would pass production)", ""]
    lines = ["Production rejected because:", ""]
    for f in failures:
        lines.append(str(f["gate"]))
    lines.append("")
    return lines


def format_validation_telegram_s11(
    *,
    candidate: CandidateG31,
    signal_id: int,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
) -> str:
    conf = float(candidate.confidence or 0)
    ms = float(candidate.market_score or 0)
    rr = float(candidate.rr or 0)
    vol = float(candidate.volume_score or 0)
    funding = candidate.funding_score
    direction = candidate.direction or "SHORT"
    vol_thr = float(PRODUCTION_PROFILE.min_volume)

    lines = [
        VALIDATION_HEADER,
        "",
        "Symbol",
        candidate.symbol,
        "",
        "Direction",
        direction,
        "",
        "Confidence",
        f"{conf:.1f}",
        "",
        "Market Score",
        f"{ms:.0f}",
        "",
        "RR",
        f"{rr:.1f}",
        "",
        "Funding",
        "PASS" if funding is not None else "FAIL",
        "",
        "Volume",
        "PASS" if vol >= vol_thr else f"FAIL ({vol:.0f}<{vol_thr:g})",
        "",
    ]
    lines.extend(format_production_rejection_block_s11(candidate))
    lines.extend([
        "This signal was emitted only to validate the pipeline.",
        "",
        "Entry",
        f"{entry:.6g}",
        "",
        "Stop",
        f"{sl:.6g}",
        "",
        "TP1",
        f"{tp1:.6g}",
        "",
        "TP2",
        f"{tp2:.6g}",
        "",
        "Signal ID",
        str(signal_id),
    ])
    return "\n".join(lines)


def persist_validation_signal_s11(
    conn: Any,
    *,
    snapshot_id: int,
    candidate: CandidateG31,
    event_id: int | None = None,
) -> ValidationSignalS11 | None:
    direction = candidate.direction or "SHORT"
    conf = float(candidate.confidence or 0)
    ms = float(candidate.market_score or 0)
    liq_pct = float(candidate.liquidity_score or 0)
    rr = float(candidate.rr or 0)
    vol = float(candidate.volume_score or 0)

    price = _resolve_price(conn, symbol=candidate.symbol, snapshot_id=snapshot_id)
    from bot.research.market_events.signal_intelligence.risk_reward_f5 import compute_risk_reward_f5
    liq_prob = liq_pct / 100.0
    rr_obj = compute_risk_reward_f5(
        expected_target_pct=2.5,
        expected_stop_pct=1.0,
        reversal_probability=min(0.95, liq_prob * 0.6 + (conf / 10.0) * 0.4),
        dynamic_confidence=conf,
        historical_reversal_rate=0.75,
    )
    plan = compute_trade_plan_f71(
        price=price,
        shock_direction=shock_direction_for_trade_side(direction),
        risk_reward=rr_obj,
        final_confidence=conf,
    )
    geom = assert_sendable_geometry_s12(
        direction=direction,
        entry=plan.entry,
        tp1=plan.tp1,
        tp2=plan.tp2,
        sl=plan.sl,
        tp3=plan.tp3,
        context=f"validation {candidate.symbol}",
    )
    if not geom.ok:
        return None

    signal_uuid = str(uuid.uuid4())
    now = int(time.time())
    prod_fail = threshold_failures_g39(candidate, PRODUCTION_PROFILE)

    # Insert first to get id for telegram card
    signal_id = insert_returning_id(
        conn,
        f"""
        INSERT INTO {_SIGNALS} (
          signal_uuid, signal_type, snapshot_id, event_id, symbol, direction,
          entry, tp1, tp2, tp3, sl,
          confidence, market_score, liquidity, rr, volume, funding_score, btc_regime,
          production_rejection_json, market_snapshot_json,
          telegram_rendered, telegram_sent, status, grade, result_label,
          pnl_pct, max_profit, max_drawdown, holding_time_sec,
          tp1_hit, tp2_hit, tp3_hit, sl_hit,
          result_telegram_sent, closed_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                  ?, NULL, NULL, NULL, 0, 0, 0, 0, 0, 0, 0, 0, NULL, ?)
        """,
        (
            signal_uuid, SIGNAL_TYPE, snapshot_id, event_id, candidate.symbol, direction,
            plan.entry, plan.tp1, plan.tp2, plan.tp3, plan.sl,
            conf, ms, liq_pct, rr, vol, candidate.funding_score, candidate.btc_alignment,
            json.dumps(prod_fail, ensure_ascii=False),
            json.dumps({"snapshot_id": snapshot_id, "symbol": candidate.symbol, "ts": now}),
            "",  # filled below
            STATUS_OPEN, now,
        ),
    )

    telegram = format_validation_telegram_s11(
        candidate=candidate,
        signal_id=signal_id,
        entry=float(plan.entry or 0),
        sl=float(plan.sl or 0),
        tp1=float(plan.tp1 or 0),
        tp2=float(plan.tp2 or 0),
    )
    conn.execute(
        f"UPDATE {_SIGNALS} SET telegram_rendered = ? WHERE id = ?",
        (telegram, signal_id),
    )
    return ValidationSignalS11(
        signal_id=signal_id,
        signal_uuid=signal_uuid,
        symbol=candidate.symbol,
        telegram_rendered=telegram,
    )


def send_validation_telegram_s11(
    conn: Any,
    signal: ValidationSignalS11,
    *,
    force_send: bool = False,
) -> bool:
    from bot.research.market_events.alert_config import (
        alert_shock_enabled,
        alerts_enabled,
        resolve_alert_chat_id,
    )
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert

    ok = False
    if alerts_enabled() and alert_shock_enabled():
        ok = _safe_alert(
            conn,
            event_id=0,
            alert_type=ALERT_SHOCK,
            detail=f"s11-validation-{signal.signal_uuid}",
            message=signal.telegram_rendered,
            enabled=True,
        )
    elif force_send:
        # validation-force: deliver even when ME_TELEGRAM_ALERTS_ENABLED=false
        try:
            from bot.research.market_events.alert_engine.telegram_delivery import deliver_telegram

            resolution = resolve_alert_chat_id()
            if resolution.chat_id:
                result = deliver_telegram(
                    signal.telegram_rendered,
                    conn=conn,
                    alert_type="VALIDATION_SIGNAL",
                    event_id=0,
                    chat_id=resolution.chat_id,
                )
                ok = bool(result.ok)
            else:
                logger.warning(
                    "validation force-send: no chat_id (%s)",
                    resolution.error or "unset",
                )
        except Exception as exc:
            logger.warning("validation force-send failed: %s", exc)
            ok = False
    else:
        logger.warning("validation telegram skipped: ME_TELEGRAM_ALERTS_ENABLED=false")

    if ok:
        conn.execute(
            f"UPDATE {_SIGNALS} SET telegram_sent = 1 WHERE id = ?",
            (signal.signal_id,),
        )
    return ok


def emit_validation_signal_s11(
    conn: Any,
    *,
    snapshot_id: int,
    candidates: list[CandidateG31],
    force: bool = False,
    event_id: int | None = None,
) -> ValidationSignalS11 | None:
    """Create at most 1 Validation Signal / day (unless force)."""
    if not force and _validation_count_today(conn) >= MAX_PER_DAY:
        logger.info("VALIDATION skip: already %s today", _validation_count_today(conn))
        return None

    candidate = pick_validation_candidate_s11(candidates)
    if candidate is None:
        logger.warning("VALIDATION skip: no candidates")
        return None

    open_syms = _open_validation_symbols(conn)
    if not force and candidate.symbol in open_syms:
        # pick next best
        sorted_all = sorted(
            candidates,
            key=lambda c: (float(c.confidence or 0), float(c.market_score or 0), float(c.rr or 0)),
            reverse=True,
        )
        candidate = next((c for c in sorted_all if c.symbol not in open_syms), None)
        if candidate is None:
            logger.info("VALIDATION skip: all top symbols already OPEN")
            return None

    signal = persist_validation_signal_s11(
        conn, snapshot_id=snapshot_id, candidate=candidate, event_id=event_id,
    )
    if not signal:
        return None
    sent = send_validation_telegram_s11(conn, signal, force_send=force)
    logger.info(
        "VALIDATION SIGNAL created id=%s symbol=%s telegram=%s",
        signal.signal_id, signal.symbol, sent,
    )
    return signal


def maybe_run_validation_lane_s11(
    conn: Any,
    *,
    snapshot_id: int,
    candidates: list[CandidateG31],
) -> ValidationSignalS11 | None:
    """Called each g3 cycle — emit guaranteed daily validation if missing."""
    try:
        return emit_validation_signal_s11(
            conn, snapshot_id=snapshot_id, candidates=candidates, force=False,
        )
    except Exception as exc:
        logger.warning("validation lane failed: %s", exc)
        return None


def format_validation_result_telegram_s11(row: dict[str, Any]) -> str:
    pnl = float(row.get("pnl_pct") or 0)
    emoji = "🟢" if pnl > 0 else "🔴"
    tp_hit = "yes" if (row.get("tp1_hit") or row.get("tp2_hit") or row.get("tp3_hit")) else "no"
    return "\n".join([
        f"{emoji} {VALIDATION_RESULT_HEADER}",
        "",
        str(row.get("symbol") or "?"),
        "",
        "Max Profit",
        f"{float(row.get('max_profit') or 0):+.2f}%",
        "",
        "Drawdown",
        f"{float(row.get('max_drawdown') or 0):+.2f}%",
        "",
        "TP hit",
        tp_hit,
        "",
        "Final PnL",
        f"{pnl:+.2f}%",
        "",
        "Grade",
        "",
        str(row.get("grade") or "—"),
    ])


def _close_validation_signal_s11(conn: Any, row: Any, *, now: int, price: float) -> None:
    is_long = row["direction"] == "LONG"
    entry = float(row["entry"] or 0)
    pnl = _pnl_pct(entry, price, is_long=is_long)
    holding = now - int(row["created_at"])
    tp1 = float(row["tp1"] or 0)
    tp2 = float(row["tp2"] or 0)
    tp3 = float(row["tp3"] or 0)
    sl = float(row["sl"] or 0)

    tp1_hit = bool(row["tp1_hit"]) or ((is_long and price >= tp1) or (not is_long and price <= tp1))
    tp2_hit = bool(row["tp2_hit"]) or ((is_long and price >= tp2) or (not is_long and price <= tp2))
    tp3_hit = bool(row["tp3_hit"]) or ((is_long and price >= tp3) or (not is_long and price <= tp3))
    sl_hit = bool(row["sl_hit"]) or ((is_long and price <= sl) or (not is_long and price >= sl))

    max_profit = max(float(row["max_profit"] or 0), pnl)
    max_dd = min(float(row["max_drawdown"] or 0), pnl)

    if tp3_hit:
        result_label = "TP3 HIT"
    elif tp2_hit:
        result_label = "TP2 HIT"
    elif tp1_hit:
        result_label = "TP1 HIT"
    elif sl_hit:
        result_label = "SL"
    else:
        result_label = "TIME"

    grade = compute_grade_g40(
        pnl_pct=pnl,
        max_profit=max_profit,
        max_drawdown=max_dd,
        rr=float(row["rr"] or 0),
        holding_sec=holding,
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
        tp3_hit=tp3_hit,
        sl_hit=sl_hit,
    )
    # Map A+ → A for validation card grades A/B/C/D/F
    if grade == "A+":
        grade = "A"

    conn.execute(
        f"""
        UPDATE {_SIGNALS} SET
          status = ?, result_label = ?, grade = ?, pnl_pct = ?,
          max_profit = ?, max_drawdown = ?, holding_time_sec = ?,
          tp1_hit = ?, tp2_hit = ?, tp3_hit = ?, sl_hit = ?,
          closed_at = ?
        WHERE id = ?
        """,
        (
            STATUS_CLOSED, result_label, grade, pnl,
            max_profit, max_dd, holding,
            int(tp1_hit), int(tp2_hit), int(tp3_hit), int(sl_hit),
            now, row["id"],
        ),
    )

    # Intentionally NOT writing to market_learning_dataset / optimizer.

    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert
    if alert_shock_enabled() and not row["result_telegram_sent"]:
        row_dict = dict(row)
        row_dict.update({
            "pnl_pct": pnl,
            "max_profit": max_profit,
            "max_drawdown": max_dd,
            "grade": grade,
            "tp1_hit": tp1_hit,
            "tp2_hit": tp2_hit,
            "tp3_hit": tp3_hit,
        })
        msg = format_validation_result_telegram_s11(row_dict)
        ok = _safe_alert(
            conn,
            event_id=0,
            alert_type=ALERT_SHOCK,
            detail=f"s11-validation-result-{row['signal_uuid']}",
            message=msg,
            enabled=True,
        )
        if ok:
            conn.execute(
                f"UPDATE {_SIGNALS} SET result_telegram_sent = 1 WHERE id = ?",
                (row["id"],),
            )


def check_validation_followups_s11(conn: Any) -> int:
    """Horizon snapshots 15m/1h/4h/24h + close at 24h or TP3/SL."""
    rows = conn.execute(
        f"SELECT * FROM {_SIGNALS} WHERE status = ? ORDER BY created_at ASC",
        (STATUS_OPEN,),
    ).fetchall()
    if not rows:
        return 0

    now = int(time.time())
    updates = 0
    for row in rows:
        price = _current_price(conn, row["symbol"])
        if not price or not row["entry"]:
            continue

        is_long = row["direction"] == "LONG"
        entry = float(row["entry"])
        pnl = _pnl_pct(entry, price, is_long=is_long)
        age = now - int(row["created_at"])
        max_profit = max(float(row["max_profit"] or 0), pnl)
        max_dd = min(float(row["max_drawdown"] or 0), pnl)

        tp1 = float(row["tp1"] or 0)
        tp2 = float(row["tp2"] or 0)
        tp3 = float(row["tp3"] or 0)
        sl = float(row["sl"] or 0)
        tp1_hit = bool(row["tp1_hit"]) or ((is_long and price >= tp1) or (not is_long and price <= tp1))
        tp2_hit = bool(row["tp2_hit"]) or ((is_long and price >= tp2) or (not is_long and price <= tp2))
        tp3_hit = bool(row["tp3_hit"]) or ((is_long and price >= tp3) or (not is_long and price <= tp3))
        sl_hit = bool(row["sl_hit"]) or ((is_long and price <= sl) or (not is_long and price >= sl))

        conn.execute(
            f"""
            UPDATE {_SIGNALS} SET max_profit = ?, max_drawdown = ?,
              tp1_hit = ?, tp2_hit = ?, tp3_hit = ?, sl_hit = ?, holding_time_sec = ?
            WHERE id = ?
            """,
            (max_profit, max_dd, int(tp1_hit), int(tp2_hit), int(tp3_hit), int(sl_hit), age, row["id"]),
        )

        for label, sec in VALIDATION_HORIZON_SECS:
            if age < sec:
                continue
            exists = conn.execute(
                f"SELECT id FROM {_HORIZONS} WHERE signal_id = ? AND horizon_label = ?",
                (row["id"], label),
            ).fetchone()
            if exists:
                continue
            insert_returning_id(
                conn,
                f"""
                INSERT INTO {_HORIZONS} (
                  signal_id, horizon_label, horizon_sec, checked_at,
                  current_price, pnl, max_profit, max_drawdown,
                  tp1_hit, tp2_hit, tp3_hit, sl_hit, holding_time_sec
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], label, sec, now, price, pnl,
                    max_profit, max_dd,
                    int(tp1_hit), int(tp2_hit), int(tp3_hit), int(sl_hit), age,
                ),
            )
            updates += 1

        should_close = sl_hit or tp3_hit or age >= 86400
        if should_close:
            _close_validation_signal_s11(conn, row, now=now, price=price)
            updates += 1

    return updates


def load_latest_candidates_s11(conn: Any) -> tuple[int | None, list[CandidateG31]]:
    """Rebuild candidate objects from latest candidate_g31 rows + last snapshot."""
    from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3

    snap = conn.execute(
        "SELECT id FROM market_snapshots_g3 ORDER BY snapshot_ts DESC LIMIT 1",
    ).fetchone()
    snapshot_id = int(snap["id"]) if snap else None

    rows = conn.execute(
        """
        SELECT * FROM market_candidate_g31
        ORDER BY candidate_ts DESC, confidence DESC
        LIMIT 40
        """,
    ).fetchall()
    if not rows:
        return snapshot_id, []
    latest_ts = int(rows[0]["candidate_ts"])
    cycle_rows = [r for r in rows if int(r["candidate_ts"]) == latest_ts]

    out: list[CandidateG31] = []
    for r in cycle_rows:
        direction = str(r["direction"] or "SHORT")
        trend_score = float(r["trend_score"] or 0) if r["trend_score"] is not None else 0.0
        trend = TrendWindowG3(
            symbol=str(r["symbol"]),
            window_minutes=60,
            pattern_type="from_db",
            consecutive_candles=0,
            trend_score=trend_score,
            direction=direction,
            details={},
        )
        out.append(CandidateG31(
            symbol=str(r["symbol"]),
            trend_score=trend_score,
            market_score=float(r["market_score"]) if r["market_score"] is not None else None,
            liquidity_score=float(r["liquidity_score"]) if r["liquidity_score"] is not None else None,
            confidence=float(r["confidence"]) if r["confidence"] is not None else None,
            rr=float(r["rr"]) if r["rr"] is not None else None,
            btc_alignment=str(r["btc_alignment"] or ""),
            funding_score=float(r["funding_score"]) if r["funding_score"] is not None else None,
            oi_score=float(r["oi_score"]) if r["oi_score"] is not None else None,
            volume_score=float(r["volume_score"]) if r["volume_score"] is not None else None,
            atr_score=float(r["atr_score"]) if r["atr_score"] is not None else None,
            fear_greed=float(r["fear_greed"]) if r["fear_greed"] is not None else None,
            candidate_state=str(r["candidate_state"] or ""),
            rejection_reason=None,
            direction=direction,
            trend_coverage_pct=None,
            trend=trend,
        ))
    return snapshot_id, out


def force_validation_signal_s11(conn: Any) -> ValidationSignalS11 | None:
    """validation-force: rebuild from latest candidates / live cycle pieces."""
    from bot.research.market_events.signal_intelligence.candidate_g31 import (
        load_g31_universe_symbols,
        run_candidate_pipeline_g31,
    )
    from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import (
        compute_liquidity_state_g3,
        persist_liquidity_state_g3,
    )
    from bot.research.market_events.signal_intelligence.recorder_g3 import record_market_snapshot_g3
    from bot.research.market_events.signal_intelligence.trend_windows_g3 import run_trend_detection_g3

    snapshot_id, payload = record_market_snapshot_g3(conn)
    universe = load_g31_universe_symbols(conn)
    trends = run_trend_detection_g3(conn, snapshot_id=snapshot_id, symbols=universe)
    liquidity = compute_liquidity_state_g3(conn, snapshot_id=snapshot_id)
    persist_liquidity_state_g3(conn, snapshot_id=snapshot_id, state=liquidity)
    candidates = run_candidate_pipeline_g31(
        conn, snapshot_id=snapshot_id, trends=trends, liquidity=liquidity,
    )
    if not candidates:
        # fallback to DB rows
        sid, candidates = load_latest_candidates_s11(conn)
        snapshot_id = sid or snapshot_id
    return emit_validation_signal_s11(
        conn, snapshot_id=snapshot_id, candidates=candidates, force=True,
    )


def format_validation_open_s11(conn: Any) -> str:
    ts = int(time.time())
    day_start = ts - (ts % 86400)
    today = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_SIGNALS} WHERE created_at >= ?",
        (day_start,),
    ).fetchone()
    open_rows = conn.execute(
        f"""
        SELECT id, symbol, direction, confidence, market_score, rr, status, created_at, telegram_sent
        FROM {_SIGNALS} WHERE status = ? ORDER BY created_at DESC
        """,
        (STATUS_OPEN,),
    ).fetchall()
    lines = [
        "Validation Open",
        "",
        "Signals today",
        str(int(today["n"] or 0) if today else 0),
        "",
        "Open",
        str(len(open_rows)),
        "",
    ]
    if not open_rows:
        lines.append("(none)")
        return "\n".join(lines)
    for r in open_rows:
        lines.extend([
            f"#{r['id']} {r['symbol']} {r['direction']}",
            f"conf={float(r['confidence'] or 0):.1f} ms={float(r['market_score'] or 0):.0f} "
            f"rr={float(r['rr'] or 0):.1f}",
            f"telegram={'yes' if r['telegram_sent'] else 'no'}",
            "",
        ])
    return "\n".join(lines).rstrip()


def format_validation_report_s11(conn: Any, *, days: int = 7) -> str:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        f"""
        SELECT id, symbol, direction, status, grade, pnl_pct, max_profit, max_drawdown,
               result_label, created_at, closed_at, telegram_sent
        FROM {_SIGNALS}
        WHERE created_at >= ?
        ORDER BY created_at DESC
        """,
        (since,),
    ).fetchall()
    closed = [r for r in rows if r["status"] == STATUS_CLOSED]
    grades: dict[str, int] = {}
    for r in closed:
        g = str(r["grade"] or "?")
        grades[g] = grades.get(g, 0) + 1
    lines = [
        "Validation Report",
        "",
        f"Days {days}",
        "",
        "Total",
        str(len(rows)),
        "",
        "Closed",
        str(len(closed)),
        "",
        "Open",
        str(sum(1 for r in rows if r["status"] == STATUS_OPEN)),
        "",
        "Grades",
        ", ".join(f"{k}:{v}" for k, v in sorted(grades.items())) or "—",
        "",
        "Note",
        "Validation signals are NOT used in Optimizer / Weight Learning / Production.",
        "",
    ]
    for r in rows[:20]:
        pnl = r["pnl_pct"]
        pnl_s = f"{float(pnl):+.2f}%" if pnl is not None else "—"
        lines.extend([
            f"#{r['id']} {r['symbol']} {r['status']} grade={r['grade'] or '—'} pnl={pnl_s}",
            "",
        ])
    return "\n".join(lines).rstrip()
