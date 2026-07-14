"""Phase G.4.0 — Shadow Signal Lane (learning mode, research only).

Isolated from production, paper, live, optimizer, and weight learning.
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
from bot.research.market_events.signal_intelligence.config import (
    G40_SHADOW_ENABLED,
    G40_SHADOW_MAX_PER_CYCLE,
    G40_SHADOW_MAX_PER_DAY,
    G40_SHADOW_MIN_CONFIDENCE,
    G40_SHADOW_MIN_LIQUIDITY_PROB,
    G40_SHADOW_MIN_MARKET_SCORE,
    G40_SHADOW_MIN_RR,
    G40_SHADOW_MIN_VOLUME,
)
from bot.research.market_events.signal_intelligence.experimental_g39 import (
    PRODUCTION_PROFILE,
    ThresholdProfileG39,
    threshold_failures_g39,
)
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import LiquidityStateG3
from bot.research.market_events.signal_intelligence.trade_plan_f71 import compute_trade_plan_f71

logger = logging.getLogger(__name__)

_SIGNALS = "market_shadow_signals"
_HORIZONS = "market_shadow_horizons"
_LEARNING = "market_learning_dataset"

STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"

SHADOW_HEADER = "🧪 SHADOW SIGNAL"

SHADOW_PROFILE = ThresholdProfileG39(
    name="shadow",
    min_confidence=G40_SHADOW_MIN_CONFIDENCE,
    min_market_score=G40_SHADOW_MIN_MARKET_SCORE,
    min_liquidity_prob=G40_SHADOW_MIN_LIQUIDITY_PROB,
    min_rr=G40_SHADOW_MIN_RR,
    min_volume=G40_SHADOW_MIN_VOLUME,
)

SHADOW_HORIZON_SECS: tuple[tuple[str, int], ...] = (
    ("15m", 900),
    ("30m", 1800),
    ("1h", 3600),
    ("2h", 7200),
    ("4h", 14400),
    ("12h", 43200),
    ("24h", 86400),
)


@dataclass(frozen=True)
class ShadowSignalG40:
    signal_id: int
    signal_uuid: str
    symbol: str
    telegram_rendered: str


def passes_shadow_g40(candidate: CandidateG31) -> bool:
    if candidate.confidence is None or candidate.market_score is None:
        return False
    if not candidate.trend or not candidate.direction:
        return False
    if float(candidate.trend_score or 0) <= 0:
        return False
    return len(threshold_failures_g39(candidate, SHADOW_PROFILE)) == 0


def is_shadow_only_g40(candidate: CandidateG31) -> bool:
    return passes_shadow_g40(candidate) and not (
        len(threshold_failures_g39(candidate, PRODUCTION_PROFILE)) == 0
    )


def format_production_rejection_shadow_g40(candidate: CandidateG31) -> str:
    failures = threshold_failures_g39(candidate, PRODUCTION_PROFILE)
    lines = ["Почему не прошёл Production", ""]
    for f in failures:
        gate = f["gate"]
        val = f["value"]
        thr = f["threshold"]
        if gate == "Liquidity":
            lines.extend([f"{gate} {val:g} < {thr:g}", ""])
        else:
            lines.extend([f"{gate} {val:g} < {thr:g}", ""])
    return "\n".join(lines).rstrip()


def _claude_summary_at_signal(conn: Any, *, symbol: str, candidate: CandidateG31) -> str:
    trend = candidate.trend
    pattern = trend.pattern_type if trend else "unknown"
    return (
        f"Shadow learning signal on {symbol}. "
        f"Pattern {pattern}, coverage {candidate.trend_coverage_pct or 0:.0f}%, "
        f"regime {candidate.btc_alignment}. Research-only lane."
    )


def format_shadow_telegram_g40(
    *,
    candidate: CandidateG31,
    claude_summary: str,
) -> str:
    direction = candidate.direction or "SHORT"
    conf = float(candidate.confidence or 0)
    ms = float(candidate.market_score or 0)
    liq = float(candidate.liquidity_score or 0)
    rr = float(candidate.rr or 0)
    rejection = format_production_rejection_shadow_g40(candidate)
    return "\n".join([
        SHADOW_HEADER,
        "",
        f"{candidate.symbol} {direction}",
        "",
        "Confidence",
        f"{conf:.1f}",
        "",
        "Market Score",
        f"{ms:.0f}",
        "",
        "Liquidity",
        f"{liq:.0f}%",
        "",
        "RR",
        f"{rr:.1f}",
        "",
        rejection,
        "",
        "------------------",
        "",
        "Claude",
        "",
        claude_summary,
    ])


def _resolve_price(conn: Any, *, symbol: str, snapshot_id: int) -> float:
    col = symbol.lower()
    if col in ("btc", "eth", "sol", "bnb"):
        snap = conn.execute(
            f"SELECT {col}_price AS p FROM market_snapshots_g3 WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        if snap and snap["p"]:
            return float(snap["p"])
    from bot.research.market_events.signal_intelligence.candles import load_recent_candles
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=2)
    if bars:
        return float(bars[-1].close)
    return 100.0


def _market_snapshot_json(conn: Any, *, snapshot_id: int, symbol: str) -> str:
    row = conn.execute("SELECT * FROM market_snapshots_g3 WHERE id = ?", (snapshot_id,)).fetchone()
    liq = conn.execute(
        "SELECT * FROM market_liquidity_state_g3 WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    payload = {
        "snapshot": dict(row) if row else {},
        "liquidity": dict(liq) if liq else {},
        "symbol": symbol,
        "ts": int(time.time()),
    }
    return json.dumps(payload, default=str)


def _shadow_sent_today(conn: Any) -> int:
    ts = int(time.time())
    day_start = ts - (ts % 86400)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_SIGNALS} WHERE created_at >= ?",
        (day_start,),
    ).fetchone()
    return int(row["n"] or 0) if row else 0


def _open_shadow_symbols(conn: Any) -> set[str]:
    rows = conn.execute(
        f"SELECT symbol FROM {_SIGNALS} WHERE status = ?",
        (STATUS_OPEN,),
    ).fetchall()
    return {str(r["symbol"]) for r in rows}


def pick_shadow_candidates_g40(candidates: list[CandidateG31], *, limit: int) -> list[CandidateG31]:
    eligible = [c for c in candidates if is_shadow_only_g40(c)]
    eligible.sort(key=lambda c: (c.confidence or 0, c.market_score or 0), reverse=True)
    return eligible[:limit]


def create_shadow_signal_g40(
    conn: Any,
    *,
    snapshot_id: int,
    candidate: CandidateG31,
    event_id: int | None = None,
) -> ShadowSignalG40 | None:
    if not candidate.trend:
        return None

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
        shock_direction="DOWN" if direction == "SHORT" else "UP",
        risk_reward=rr_obj,
        final_confidence=conf,
    )

    claude_summary = _claude_summary_at_signal(conn, symbol=candidate.symbol, candidate=candidate)
    telegram = format_shadow_telegram_g40(candidate=candidate, claude_summary=claude_summary)
    signal_uuid = str(uuid.uuid4())
    now = int(time.time())

    signal_id = insert_returning_id(
        conn,
        f"""
        INSERT INTO {_SIGNALS} (
          signal_uuid, snapshot_id, event_id, symbol, direction,
          entry, tp1, tp2, tp3, sl,
          confidence, market_score, liquidity, rr, volume, btc_regime,
          claude_summary, production_rejection_json, market_snapshot_json,
          telegram_rendered, telegram_sent, status, grade, result_label,
          pnl_pct, max_profit, max_drawdown, holding_time_sec,
          tp1_hit, tp2_hit, tp3_hit, sl_hit, claude_review_json,
          closed_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                  ?, NULL, NULL, NULL, 0, 0, 0, 0, 0, 0, 0, NULL, NULL, ?)
        """,
        (
            signal_uuid, snapshot_id, event_id, candidate.symbol, direction,
            plan.entry, plan.tp1, plan.tp2, plan.tp3, plan.sl,
            conf, ms, liq_pct, rr, vol, candidate.btc_alignment,
            claude_summary,
            json.dumps(threshold_failures_g39(candidate, PRODUCTION_PROFILE), ensure_ascii=False),
            _market_snapshot_json(conn, snapshot_id=snapshot_id, symbol=candidate.symbol),
            telegram, STATUS_OPEN, now,
        ),
    )
    return ShadowSignalG40(
        signal_id=signal_id,
        signal_uuid=signal_uuid,
        symbol=candidate.symbol,
        telegram_rendered=telegram,
    )


def send_shadow_telegram_g40(conn: Any, signal: ShadowSignalG40) -> bool:
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert

    if not alert_shock_enabled():
        return False
    ok = _safe_alert(
        conn,
        event_id=0,
        alert_type=ALERT_SHOCK,
        detail=f"g40-shadow-{signal.signal_uuid}",
        message=signal.telegram_rendered,
        enabled=True,
    )
    if ok:
        conn.execute(
            f"UPDATE {_SIGNALS} SET telegram_sent = 1 WHERE id = ?",
            (signal.signal_id,),
        )
    return ok


def maybe_run_shadow_lane_g40(
    conn: Any,
    *,
    snapshot_id: int,
    candidates: list[CandidateG31],
    event_id: int | None = None,
) -> list[ShadowSignalG40]:
    if not G40_SHADOW_ENABLED:
        return []

    sent_today = _shadow_sent_today(conn)
    if sent_today >= G40_SHADOW_MAX_PER_DAY:
        return []

    remaining = G40_SHADOW_MAX_PER_DAY - sent_today
    per_cycle = min(G40_SHADOW_MAX_PER_CYCLE, remaining)
    open_syms = _open_shadow_symbols(conn)

    picked = [
        c for c in pick_shadow_candidates_g40(candidates, limit=per_cycle)
        if c.symbol not in open_syms
    ]

    created: list[ShadowSignalG40] = []
    for cand in picked:
        recent = conn.execute(
            f"""
            SELECT id FROM {_SIGNALS}
            WHERE symbol = ? AND created_at >= ?
            """,
            (cand.symbol, int(time.time()) - 7200),
        ).fetchone()
        if recent:
            continue
        sig = create_shadow_signal_g40(
            conn, snapshot_id=snapshot_id, candidate=cand, event_id=event_id,
        )
        if sig:
            send_shadow_telegram_g40(conn, sig)
            created.append(sig)
    return created


def _current_price(conn: Any, symbol: str) -> float | None:
    from bot.research.market_events.signal_intelligence.candles import load_recent_candles
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=2)
    if not bars:
        return None
    return float(bars[-1].close)


def _pnl_pct(entry: float, price: float, *, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (price / entry - 1.0) * 100.0
    return (1.0 - price / entry) * 100.0


def _format_holding(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def compute_grade_g40(
    *,
    pnl_pct: float,
    max_profit: float,
    max_drawdown: float,
    rr: float,
    holding_sec: int,
    tp1_hit: bool,
    tp2_hit: bool,
    tp3_hit: bool,
    sl_hit: bool,
) -> str:
    if sl_hit and holding_sec < 900:
        return "F"
    if tp3_hit and pnl_pct >= 3.0 and max_drawdown >= -1.0:
        return "A+"
    if tp3_hit and pnl_pct > 0:
        return "A"
    if tp2_hit and pnl_pct >= 2.0:
        return "A"
    if tp2_hit and pnl_pct > 0:
        return "B"
    if tp1_hit and pnl_pct > 0:
        return "B"
    if sl_hit:
        return "F" if pnl_pct <= -2.0 else "D"
    if pnl_pct >= 1.0:
        return "C"
    if pnl_pct > 0:
        return "C"
    if pnl_pct <= -1.5:
        return "F"
    return "D"


def build_claude_review_g40(row: dict[str, Any]) -> dict[str, Any]:
    grade = row.get("grade") or "D"
    pnl = float(row.get("pnl_pct") or 0)
    sym = row.get("symbol")
    review = {
        "why_good": "",
        "why_bad": "",
        "overrated": [],
        "underrated": [],
        "changes": [],
        "confidence": 0.7,
    }
    if grade in ("A+", "A", "B"):
        review["why_good"] = (
            f"{sym} shadow signal worked: grade {grade}, PnL {pnl:+.1f}%. "
            "Trend and liquidity alignment were sufficient for shadow thresholds."
        )
        review["why_bad"] = "Production gates were too strict for this setup."
        review["underrated"] = ["Liquidity sweep context", "Trend persistence"]
        review["overrated"] = ["Absolute market score threshold"]
        review["changes"] = ["Consider lowering MS gate for similar patterns"]
    else:
        review["why_bad"] = (
            f"{sym} failed: grade {grade}, PnL {pnl:+.1f}%. "
            "Shadow thresholds may still be too permissive for this regime."
        )
        review["why_good"] = "Useful negative example for learning dataset."
        review["overrated"] = ["Confidence at signal time", "Short-term trend score"]
        review["underrated"] = ["BTC regime conflict", "Volume quality"]
        review["changes"] = ["Tighten RR or liquidity for similar setups"]
    return review


def _try_claude_review_g40(row: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        return review
    try:
        from bot.research.market_events.signal_intelligence.research_g2 import call_claude_research_g2
        prompt = (
            f"Shadow signal closed: {row.get('symbol')} {row.get('direction')} "
            f"grade={row.get('grade')} pnl={row.get('pnl_pct')}. "
            "Return JSON with keys: why_good, why_bad, overrated, underrated, changes, confidence."
        )
        raw = call_claude_research_g2(prompt, max_tokens=400)
        if raw:
            from bot.research.market_events.signal_intelligence.claude_json_parser_g501 import (
                extract_json_from_claude_text,
            )
            parsed = extract_json_from_claude_text(raw)
            if isinstance(parsed, dict):
                review.update({k: parsed.get(k, review.get(k)) for k in review})
    except Exception as exc:
        logger.debug("shadow claude review skipped: %s", exc)
    return review


def _append_learning_dataset_g40(conn: Any, *, signal_id: int, row: dict[str, Any], review: dict) -> None:
    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_LEARNING} (
          lane, signal_id, symbol, direction, grade, result_label, pnl_pct,
          market_snapshot_json, claude_review_json, created_at
        ) VALUES ('shadow', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            row["symbol"],
            row["direction"],
            row.get("grade"),
            row.get("result_label"),
            row.get("pnl_pct"),
            row.get("market_snapshot_json"),
            json.dumps(review, ensure_ascii=False),
            int(time.time()),
        ),
    )


def format_shadow_result_telegram_g40(row: dict[str, Any], review: dict[str, Any]) -> str:
    pnl = float(row.get("pnl_pct") or 0)
    emoji = "🟢" if pnl > 0 else "🔴"
    result = row.get("result_label") or "CLOSED"
    holding = _format_holding(int(row.get("holding_time_sec") or 0))
    claude_line = review.get("why_good") or review.get("why_bad") or ""
    return "\n".join([
        f"{emoji} RESULT",
        "",
        f"{row['symbol']} {row['direction']}",
        "",
        result,
        "",
        f"{pnl:+.1f}%",
        "",
        "RR",
        f"{float(row.get('rr') or 0):.1f}",
        "",
        "Holding",
        holding,
        "",
        "Grade",
        str(row.get("grade") or "—"),
        "",
        "Claude",
        "",
        claude_line[:500],
    ])


def _close_shadow_signal_g40(conn: Any, row: Any, *, now: int, price: float) -> None:
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

    row_dict = dict(row)
    row_dict.update({
        "pnl_pct": pnl,
        "max_profit": max_profit,
        "max_drawdown": max_dd,
        "holding_time_sec": holding,
        "grade": grade,
        "result_label": result_label,
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "tp3_hit": tp3_hit,
        "sl_hit": sl_hit,
    })

    review = build_claude_review_g40(row_dict)
    review = _try_claude_review_g40(row_dict, review)

    conn.execute(
        f"""
        UPDATE {_SIGNALS} SET
          status = ?, result_label = ?, grade = ?, pnl_pct = ?,
          max_profit = ?, max_drawdown = ?, holding_time_sec = ?,
          tp1_hit = ?, tp2_hit = ?, tp3_hit = ?, sl_hit = ?,
          claude_review_json = ?, closed_at = ?
        WHERE id = ?
        """,
        (
            STATUS_CLOSED, result_label, grade, pnl,
            max_profit, max_dd, holding,
            int(tp1_hit), int(tp2_hit), int(tp3_hit), int(sl_hit),
            json.dumps(review, ensure_ascii=False), now, row["id"],
        ),
    )

    _append_learning_dataset_g40(conn, signal_id=int(row["id"]), row=row_dict, review=review)

    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert
    if alert_shock_enabled():
        msg = format_shadow_result_telegram_g40(row_dict, review)
        _safe_alert(
            conn,
            event_id=0,
            alert_type=ALERT_SHOCK,
            detail=f"g40-shadow-result-{row['signal_uuid']}",
            message=msg,
            enabled=True,
        )


def check_shadow_followups_g40(conn: Any) -> int:
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
              tp1_hit = ?, tp2_hit = ?, tp3_hit = ?, sl_hit = ?
            WHERE id = ?
            """,
            (max_profit, max_dd, int(tp1_hit), int(tp2_hit), int(tp3_hit), int(sl_hit), row["id"]),
        )

        for label, sec in SHADOW_HORIZON_SECS:
            if age < sec:
                continue
            exists = conn.execute(
                f"""
                SELECT id FROM {_HORIZONS}
                WHERE signal_id = ? AND horizon_label = ?
                """,
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
            _close_shadow_signal_g40(conn, row, now=now, price=price)
            updates += 1

    return updates


def estimate_shadow_signals_per_day_g40(conn: Any, *, days: int = 7) -> float:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        """
        SELECT symbol, confidence, market_score, liquidity_score, rr, volume_score, created_at
        FROM market_candidate_g31
        WHERE created_at >= ? AND confidence IS NOT NULL AND market_score IS NOT NULL
        """,
        (since,),
    ).fetchall()
    if not rows:
        return 0.0

    # dedupe by cycle-ish window (5 min buckets)
    seen: set[tuple[int, str]] = set()
    count = 0
    for r in rows:
        bucket = int(r["created_at"]) // 300
        sym = str(r.get("symbol") or "?")
        conf = float(r["confidence"] or 0)
        ms = float(r["market_score"] or 0)
        liq = float(r["liquidity_score"] or 0) / 100.0
        rr = float(r["rr"] or 0)
        vol = float(r["volume_score"] or 0)
        prod_ok = (
            conf >= PRODUCTION_PROFILE.min_confidence
            and ms >= PRODUCTION_PROFILE.min_market_score
            and liq >= PRODUCTION_PROFILE.min_liquidity_prob
            and rr >= PRODUCTION_PROFILE.min_rr
            and vol >= PRODUCTION_PROFILE.min_volume
        )
        shadow_ok = (
            conf >= SHADOW_PROFILE.min_confidence
            and ms >= SHADOW_PROFILE.min_market_score
            and liq >= SHADOW_PROFILE.min_liquidity_prob
            and rr >= SHADOW_PROFILE.min_rr
            and vol >= SHADOW_PROFILE.min_volume
        )
        if shadow_ok and not prod_ok:
            key = (bucket, sym)
            if key not in seen:
                seen.add(key)
                count += 1
    return round(min(G40_SHADOW_MAX_PER_DAY, count / max(1.0, days)), 1)


def shadow_stats_g40(conn: Any, *, days: int = 7) -> dict[str, Any]:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        f"""
        SELECT * FROM {_SIGNALS}
        WHERE created_at >= ?
        """,
        (since,),
    ).fetchall()
    closed = [dict(r) for r in rows if r["status"] == STATUS_CLOSED]
    wins = [r for r in closed if float(r.get("pnl_pct") or 0) > 0]
    losses = [r for r in closed if float(r.get("pnl_pct") or 0) <= 0]

    gross_win = sum(max(0.0, float(r.get("pnl_pct") or 0)) for r in closed)
    gross_loss = sum(abs(min(0.0, float(r.get("pnl_pct") or 0))) for r in closed)
    pf = gross_win / gross_loss if gross_loss > 0 else gross_win

    by_sym: dict[str, list[float]] = {}
    for r in closed:
        by_sym.setdefault(str(r["symbol"]), []).append(float(r.get("pnl_pct") or 0))

    sym_avg = {s: sum(v) / len(v) for s, v in by_sym.items() if v}
    best_sym = max(sym_avg, key=sym_avg.get) if sym_avg else "—"
    worst_sym = min(sym_avg, key=sym_avg.get) if sym_avg else "—"

    grades: dict[str, int] = {}
    for r in closed:
        g = str(r.get("grade") or "?")
        grades[g] = grades.get(g, 0) + 1
    top_grade = max(grades, key=grades.get) if grades else "—"
    worst_grade = min(grades, key=grades.get) if grades else "—"

    avg_hold = (
        sum(int(r.get("holding_time_sec") or 0) for r in closed) / len(closed)
        if closed else 0
    )
    avg_rr = sum(float(r.get("rr") or 0) for r in rows) / len(rows) if rows else 0.0

    return {
        "signals": len(rows),
        "open": sum(1 for r in rows if r["status"] == STATUS_OPEN),
        "closed": len(closed),
        "win_rate": round(len(wins) / len(closed), 3) if closed else 0.0,
        "profit_factor": round(pf, 2),
        "avg_rr": round(avg_rr, 2),
        "avg_hold_sec": int(avg_hold),
        "best": max(closed, key=lambda r: float(r.get("pnl_pct") or -999)) if closed else None,
        "worst": min(closed, key=lambda r: float(r.get("pnl_pct") or 999)) if closed else None,
        "best_symbol": best_sym,
        "worst_symbol": worst_sym,
        "top_pattern": top_grade,
        "worst_pattern": worst_grade,
        "wins": len(wins),
        "losses": len(losses),
    }


def format_shadow_report_g40(conn: Any, *, days: int = 7) -> str:
    s = shadow_stats_g40(conn, days=days)
    est = estimate_shadow_signals_per_day_g40(conn, days=days)
    lines = [
        f"Shadow Report — last {days} days",
        "",
        "Signals",
        str(s["signals"]),
        "",
        "Open",
        str(s["open"]),
        "",
        "WR",
        f"{s['win_rate'] * 100:.0f}%",
        "",
        "PF",
        f"{s['profit_factor']:.2f}",
        "",
        "RR",
        f"{s['avg_rr']:.1f}",
        "",
        "Average Hold",
        _format_holding(s["avg_hold_sec"]),
        "",
        "Best",
        (
            f"{s['best']['symbol']} {float(s['best']['pnl_pct']):+.1f}%"
            if s["best"] else "—"
        ),
        "",
        "Worst",
        (
            f"{s['worst']['symbol']} {float(s['worst']['pnl_pct']):+.1f}%"
            if s["worst"] else "—"
        ),
        "",
        "Best Symbols",
        str(s["best_symbol"]),
        "",
        "Worst Symbols",
        str(s["worst_symbol"]),
        "",
        "Top Patterns",
        str(s["top_pattern"]),
        "",
        "Worst Patterns",
        str(s["worst_pattern"]),
        "",
        "Est. signals/day (current market)",
        str(est),
    ]
    return "\n".join(lines)


def format_shadow_open_g40(conn: Any) -> str:
    rows = conn.execute(
        f"""
        SELECT symbol, direction, confidence, market_score, liquidity, rr, created_at
        FROM {_SIGNALS} WHERE status = ?
        ORDER BY created_at DESC
        """,
        (STATUS_OPEN,),
    ).fetchall()
    if not rows:
        return "No open Shadow signals."
    lines = ["Open Shadow Signals", ""]
    for r in rows:
        age = int(time.time()) - int(r["created_at"])
        lines.extend([
            f"{r['symbol']} {r['direction']}",
            f"Conf {float(r['confidence']):.1f} MS {float(r['market_score']):.0f}",
            f"Liq {float(r['liquidity']):.0f}% RR {float(r['rr']):.1f}",
            f"Age {_format_holding(age)}",
            "",
        ])
    return "\n".join(lines).rstrip()


def shadow_dashboard_g40(conn: Any, *, status: str | None = None, symbol: str | None = None) -> dict[str, Any]:
    q = f"SELECT * FROM {_SIGNALS} WHERE 1=1"
    params: list[Any] = []
    if status == "open":
        q += " AND status = ?"
        params.append(STATUS_OPEN)
    elif status == "closed":
        q += " AND status = ?"
        params.append(STATUS_CLOSED)
    if symbol:
        q += " AND symbol = ?"
        params.append(symbol.upper())
    q += " ORDER BY created_at DESC LIMIT 100"
    rows = conn.execute(q, params).fetchall()

    signals = []
    for r in rows:
        d = dict(r)
        pnl = float(d.get("pnl_pct") or 0)
        d["outcome"] = "win" if pnl > 0 else ("loss" if d.get("status") == STATUS_CLOSED else "open")
        signals.append(d)

    stats = shadow_stats_g40(conn, days=7)
    return {
        "tab": "Shadow Signals",
        "stats": stats,
        "filters": {"status": status, "symbol": symbol},
        "signals": signals,
    }


def lane_comparison_stats_g40(conn: Any, *, days: int = 7) -> dict[str, Any]:
    """Production / Shadow / Combined summary for G4/G5 reports."""
    since = int(time.time()) - days * 86400
    prod = conn.execute(
        """
        SELECT pnl_pct, risk_reward FROM market_live_signals_g3
        WHERE created_at >= ? AND status = 'CLOSED'
        """,
        (since,),
    ).fetchall()
    shadow = conn.execute(
        f"""
        SELECT pnl_pct, rr AS risk_reward FROM {_SIGNALS}
        WHERE created_at >= ? AND status = ?
        """,
        (since, STATUS_CLOSED),
    ).fetchall()

    def _summ(rows: list[Any]) -> dict[str, Any]:
        if not rows:
            return {"signals": 0, "win_rate": 0.0, "avg_rr": 0.0}
        wins = sum(1 for r in rows if float(r["pnl_pct"] or 0) > 0)
        return {
            "signals": len(rows),
            "win_rate": round(wins / len(rows), 3),
            "avg_rr": round(sum(float(r["risk_reward"] or 0) for r in rows) / len(rows), 2),
        }

    p = _summ(prod)
    s = _summ(shadow)
    combined_rows = [dict(r) for r in prod] + [dict(r) for r in shadow]
    c = _summ(combined_rows)
    return {"production": p, "shadow": s, "combined": c}
