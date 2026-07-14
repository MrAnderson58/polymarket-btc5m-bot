"""Phase G.3.9 — Experimental Signal Calibration (research only).

Never affects production thresholds, paper/live execution, weights, learning, or optimizer.
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
    G3_MIN_CONFIDENCE,
    G3_MIN_LIQUIDITY_PROB,
    G3_MIN_MARKET_SCORE,
    G3_MIN_RISK_REWARD,
    G39_EXPERIMENTAL_MODE,
    G39_MIN_CONFIDENCE,
    G39_MIN_LIQUIDITY_PROB,
    G39_MIN_MARKET_SCORE,
    G39_MIN_RISK_REWARD,
)
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import LiquidityStateG3
from bot.research.market_events.signal_intelligence.telegram_g3 import (
    format_professional_telegram_g3,
    format_result_g3,
    format_tp_hit_g3,
)
from bot.research.market_events.signal_intelligence.trade_geometry_s12 import (
    assert_sendable_geometry_s12,
    shock_direction_for_trade_side,
)
from bot.research.market_events.signal_intelligence.trade_plan_f71 import compute_trade_plan_f71
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3

logger = logging.getLogger(__name__)

_TABLE = "market_experimental_signals_g39"
_FOLLOWUP_TABLE = "market_experimental_followup_g39"

STATUS_ACTIVE = "ACTIVE"
STATUS_TP1 = "TP1_HIT"
STATUS_TP2 = "TP2_HIT"
STATUS_CLOSED = "CLOSED"

EXPERIMENTAL_HEADER = (
    "🧪 EXPERIMENTAL SIGNAL\n\n"
    "Not a production signal.\n\n"
    "This signal passed relaxed research thresholds."
)


@dataclass(frozen=True)
class ThresholdProfileG39:
    name: str
    min_confidence: float
    min_market_score: float
    min_liquidity_prob: float
    min_rr: float
    min_volume: float = 40.0


PRODUCTION_PROFILE = ThresholdProfileG39(
    name="production",
    min_confidence=G3_MIN_CONFIDENCE,
    min_market_score=G3_MIN_MARKET_SCORE,
    min_liquidity_prob=G3_MIN_LIQUIDITY_PROB,
    min_rr=G3_MIN_RISK_REWARD,
)

EXPERIMENTAL_PROFILE = ThresholdProfileG39(
    name="experimental",
    min_confidence=G39_MIN_CONFIDENCE,
    min_market_score=G39_MIN_MARKET_SCORE,
    min_liquidity_prob=G39_MIN_LIQUIDITY_PROB,
    min_rr=G39_MIN_RISK_REWARD,
)


@dataclass(frozen=True)
class ExperimentalSignalG39:
    signal_id: int
    signal_uuid: str
    symbol: str
    telegram_rendered: str


def threshold_failures_g39(
    candidate: CandidateG31,
    profile: ThresholdProfileG39,
) -> list[dict[str, Any]]:
    """Return list of failed gates for a profile."""
    conf = float(candidate.confidence or 0)
    ms = float(candidate.market_score or 0)
    liq_pct = float(candidate.liquidity_score or 0)
    liq_prob = liq_pct / 100.0
    rr = float(candidate.rr or 0)
    vol = float(candidate.volume_score or 0)

    checks: list[tuple[str, float, float, str]] = [
        ("Volume", vol, profile.min_volume, "volume"),
        ("Confidence", conf, profile.min_confidence, "confidence"),
        ("Market Score", ms, profile.min_market_score, "market_score"),
        ("Liquidity", liq_pct, profile.min_liquidity_prob * 100.0, "liquidity_pct"),
        ("RR", rr, profile.min_rr, "rr"),
    ]
    failures: list[dict[str, Any]] = []
    for label, value, threshold, key in checks:
        if value < threshold:
            failures.append({
                "gate": label,
                "value": round(value, 2) if key != "market_score" else round(value, 1),
                "threshold": threshold,
                "key": key,
            })
    return failures


def passes_profile_g39(candidate: CandidateG31, profile: ThresholdProfileG39) -> bool:
    if candidate.confidence is None or candidate.market_score is None:
        return False
    if not candidate.trend or not candidate.direction:
        return False
    if float(candidate.trend_score or 0) <= 0:
        return False
    return len(threshold_failures_g39(candidate, profile)) == 0


def is_experimental_only_g39(candidate: CandidateG31) -> bool:
    return (
        passes_profile_g39(candidate, EXPERIMENTAL_PROFILE)
        and not passes_profile_g39(candidate, PRODUCTION_PROFILE)
    )


def format_production_rejection_g39(candidate: CandidateG31) -> str:
    failures = threshold_failures_g39(candidate, PRODUCTION_PROFILE)
    lines = ["Production rejected because", ""]
    for f in failures:
        val = f["value"]
        thr = f["threshold"]
        if f["gate"] == "Liquidity":
            lines.extend([f["gate"], f"{val:g}", "<", f"{thr:g}", ""])
        elif f["gate"] == "Market Score":
            lines.extend([f["gate"], f"{val:g}", "<", f"{thr:g}", ""])
        elif f["gate"] == "RR":
            lines.extend([f["gate"], f"{val:g}", "<", f"{thr:g}", ""])
        else:
            lines.extend([f["gate"], f"{val:g}", "<", f"{thr:g}", ""])
    return "\n".join(lines).rstrip()


def pick_experimental_candidate_g39(candidates: list[CandidateG31]) -> CandidateG31 | None:
    eligible = [c for c in candidates if is_experimental_only_g39(c)]
    if not eligible:
        return None
    return max(eligible, key=lambda c: (c.confidence or 0, c.market_score or 0))


def _build_experimental_telegram(
    *,
    candidate: CandidateG31,
    liquidity: LiquidityStateG3,
    trade_plan: dict[str, Any],
    signal_uuid: str,
) -> str:
    rejection = format_production_rejection_g39(candidate)
    trend = candidate.trend
    assert trend is not None
    direction = candidate.direction or "SHORT"
    conf = float(candidate.confidence or 0)
    ms = float(candidate.market_score or 0)
    liq_prob = float(candidate.liquidity_score or 0) / 100.0
    probability = min(0.95, liq_prob * 0.6 + (conf / 10.0) * 0.4)
    trend_summary = trend.details.get("description", f"{trend.consecutive_candles} candles")

    card = format_professional_telegram_g3(
        symbol=candidate.symbol,
        direction=direction,
        confidence=conf,
        probability=probability,
        market_score=ms,
        liquidity_state=liquidity.primary_state,
        trend_summary=str(trend_summary),
        funding=liquidity.factors.get("funding"),
        oi_rising=liquidity.factors.get("oi_rising"),
        btc_context=candidate.btc_alignment,
        reasons=[candidate.rejection_reason or "Experimental threshold pass"],
        trade_plan=trade_plan,
        claude_summary=None,
        historical=[],
        signal_uuid=signal_uuid,
        paper_mode=True,
        trend_coverage_pct=candidate.trend_coverage_pct,
    )
    return f"{EXPERIMENTAL_HEADER}\n\n{rejection}\n\n{card}"


def evaluate_experimental_signal_g39(
    conn: Any,
    *,
    snapshot_id: int,
    candidates: list[CandidateG31],
    liquidity: LiquidityStateG3,
    event_id: int | None = None,
) -> ExperimentalSignalG39 | None:
    if not G39_EXPERIMENTAL_MODE:
        return None

    best = pick_experimental_candidate_g39(candidates)
    if not best or not best.trend:
        return None

    recent = conn.execute(
        f"""
        SELECT id FROM {_TABLE}
        WHERE symbol = ? AND created_at >= ? AND telegram_sent = 1
        """,
        (best.symbol, int(time.time()) - 3600),
    ).fetchone()
    if recent:
        return None

    direction = best.direction or "SHORT"
    conf = float(best.confidence or 0)
    ms = float(best.market_score or 0)
    liq_prob = float(best.liquidity_score or 0) / 100.0
    rr = float(best.rr or 0)

    price = None
    col = best.symbol.lower()
    if col in ("btc", "eth", "sol", "bnb"):
        snap = conn.execute(
            f"SELECT {col}_price AS p FROM market_snapshots_g3 WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        if snap and snap["p"]:
            price = float(snap["p"])
    if not price:
        from bot.research.market_events.signal_intelligence.candles import load_recent_candles
        bars = load_recent_candles(conn, symbol=best.symbol, venue="binance_futures", timeframe="5m", limit=2)
        if bars:
            price = float(bars[-1].close)
    price = price or 100.0

    from bot.research.market_events.signal_intelligence.risk_reward_f5 import compute_risk_reward_f5
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
        context=f"experimental {best.symbol}",
    )
    if not geom.ok:
        return None
    trade_json = {
        "entry": plan.entry,
        "sl": plan.sl,
        "tp1": plan.tp1,
        "tp2": plan.tp2,
        "tp3": plan.tp3,
        "risk_reward": plan.risk_reward,
        "position_size_pct": plan.position_size_pct,
    }

    signal_uuid = str(uuid.uuid4())
    rejection_json = json.dumps(threshold_failures_g39(best, PRODUCTION_PROFILE), ensure_ascii=False)
    telegram = _build_experimental_telegram(
        candidate=best,
        liquidity=liquidity,
        trade_plan=trade_json,
        signal_uuid=signal_uuid,
    )
    now = int(time.time())
    signal_id = insert_returning_id(
        conn,
        f"""
        INSERT INTO {_TABLE} (
          signal_uuid, snapshot_id, event_id, symbol, direction,
          confidence, market_score, liquidity_probability, risk_reward,
          reason, production_rejection_json, telegram_rendered, telegram_sent,
          result, status, entry, tp1, tp2, tp3, sl, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_uuid, snapshot_id, event_id, best.symbol, direction,
            conf, ms, liq_prob, rr,
            best.rejection_reason or "Experimental-only pass",
            rejection_json, telegram, STATUS_ACTIVE,
            plan.entry, plan.tp1, plan.tp2, plan.tp3, plan.sl, now,
        ),
    )
    return ExperimentalSignalG39(
        signal_id=signal_id,
        signal_uuid=signal_uuid,
        symbol=best.symbol,
        telegram_rendered=telegram,
    )


def send_experimental_telegram_g39(conn: Any, signal: ExperimentalSignalG39) -> bool:
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert

    if not alert_shock_enabled():
        return False

    dedupe = f"g39-experimental-{signal.signal_uuid}"
    ok = _safe_alert(
        conn,
        event_id=0,
        alert_type=ALERT_SHOCK,
        detail=dedupe,
        message=signal.telegram_rendered,
        enabled=True,
    )
    if ok:
        conn.execute(
            f"UPDATE {_TABLE} SET telegram_sent = 1 WHERE id = ?",
            (signal.signal_id,),
        )
    return ok


def maybe_run_experimental_g39(
    conn: Any,
    *,
    snapshot_id: int,
    candidates: list[CandidateG31],
    liquidity: LiquidityStateG3,
    event_id: int | None = None,
) -> ExperimentalSignalG39 | None:
    """Research-only experimental lane — isolated from production signals."""
    if not G39_EXPERIMENTAL_MODE:
        return None
    signal = evaluate_experimental_signal_g39(
        conn,
        snapshot_id=snapshot_id,
        candidates=candidates,
        liquidity=liquidity,
        event_id=event_id,
    )
    if signal:
        send_experimental_telegram_g39(conn, signal)
    return signal


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


def _send_experimental_followup(
    conn: Any,
    *,
    signal_id: int,
    followup_type: str,
    message: str,
    signal_uuid: str,
) -> bool:
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert

    if not alert_shock_enabled():
        return False
    dedupe = f"g39-followup-{followup_type}-{signal_uuid}"
    ok = _safe_alert(
        conn,
        event_id=0,
        alert_type=ALERT_SHOCK,
        detail=dedupe,
        message=f"🧪 EXPERIMENTAL\n\n{message}",
        enabled=True,
    )
    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_FOLLOWUP_TABLE} (signal_id, followup_type, telegram_sent, message_text, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (signal_id, followup_type, 1 if ok else 0, message, int(time.time())),
    )
    return ok


def check_experimental_followups_g39(conn: Any) -> int:
    """Separate follow-up for experimental signals — no learning/weight hooks."""
    rows = conn.execute(
        f"""
        SELECT id, signal_uuid, symbol, direction, status, entry, tp1, tp2, tp3, sl,
               risk_reward, created_at, max_profit_pct, max_drawdown_pct
        FROM {_TABLE}
        WHERE status IN ('ACTIVE', 'TP1_HIT')
        ORDER BY created_at ASC
        """,
    ).fetchall()
    if not rows:
        return 0

    updates = 0
    now = int(time.time())
    for r in rows:
        price = _current_price(conn, r["symbol"])
        if not price or not r["entry"]:
            continue

        is_long = r["direction"] == "LONG"
        entry = float(r["entry"])
        pnl = _pnl_pct(entry, price, is_long=is_long)
        max_profit = max(float(r["max_profit_pct"] or 0), pnl)
        max_dd = min(float(r["max_drawdown_pct"] or 0), pnl)

        status = r["status"]
        tp1 = float(r["tp1"] or 0)
        tp2 = float(r["tp2"] or 0)
        sl = float(r["sl"] or 0)

        hit_tp1 = (is_long and price >= tp1) or (not is_long and price <= tp1)
        hit_tp2 = (is_long and price >= tp2) or (not is_long and price <= tp2)
        hit_sl = (is_long and price <= sl) or (not is_long and price >= sl)

        if status == STATUS_ACTIVE and hit_tp1:
            msg = format_tp_hit_g3(level="TP1", symbol=r["symbol"], signal_uuid=r["signal_uuid"])
            _send_experimental_followup(
                conn, signal_id=int(r["id"]), followup_type="TP1",
                message=msg, signal_uuid=r["signal_uuid"],
            )
            conn.execute(
                f"UPDATE {_TABLE} SET status = ?, max_profit_pct = ?, max_drawdown_pct = ? WHERE id = ?",
                (STATUS_TP1, max_profit, max_dd, r["id"]),
            )
            updates += 1
            continue

        if status in (STATUS_ACTIVE, STATUS_TP1) and hit_tp2:
            msg = format_tp_hit_g3(level="TP2", symbol=r["symbol"], signal_uuid=r["signal_uuid"])
            _send_experimental_followup(
                conn, signal_id=int(r["id"]), followup_type="TP2",
                message=msg, signal_uuid=r["signal_uuid"],
            )
            conn.execute(
                f"UPDATE {_TABLE} SET status = ?, result = 'TP2', max_profit_pct = ?, max_drawdown_pct = ? WHERE id = ?",
                (STATUS_TP2, max_profit, max_dd, r["id"]),
            )
            updates += 1

        if hit_sl or (now - int(r["created_at"]) > 86400):
            holding = now - int(r["created_at"])
            result_label = "SL" if hit_sl else "TIME"
            msg = format_result_g3(
                symbol=r["symbol"],
                signal_uuid=r["signal_uuid"],
                pnl_pct=pnl,
                risk_reward=float(r["risk_reward"] or 0),
                holding_seconds=holding,
                max_drawdown_pct=abs(max_dd),
                max_profit_pct=max_profit,
            )
            _send_experimental_followup(
                conn, signal_id=int(r["id"]), followup_type="CLOSED",
                message=msg, signal_uuid=r["signal_uuid"],
            )
            conn.execute(
                f"""
                UPDATE {_TABLE}
                SET status = ?, result = ?, closed_at = ?, pnl_pct = ?,
                    max_profit_pct = ?, max_drawdown_pct = ?
                WHERE id = ?
                """,
                (STATUS_CLOSED, result_label, now, pnl, max_profit, max_dd, r["id"]),
            )
            updates += 1

    return updates


def _today_bounds() -> tuple[int, int]:
    ts = int(time.time())
    day_start = ts - (ts % 86400)
    return day_start, day_start + 86400 - 1


def experimental_stats_today_g39(conn: Any) -> dict[str, Any]:
    day_start, day_end = _today_bounds()
    rows = conn.execute(
        f"""
        SELECT status, result, pnl_pct, risk_reward, telegram_sent
        FROM {_TABLE}
        WHERE created_at BETWEEN ? AND ?
        """,
        (day_start, day_end),
    ).fetchall()

    signals = len(rows)
    waiting = sum(1 for r in rows if r["status"] in (STATUS_ACTIVE, STATUS_TP1))
    tp = sum(1 for r in rows if r["result"] in ("TP1", "TP2") or r["status"] == STATUS_TP2)
    sl = sum(1 for r in rows if r["result"] == "SL")
    closed = [r for r in rows if r["status"] == STATUS_CLOSED]
    wins = [r for r in closed if r["pnl_pct"] and float(r["pnl_pct"]) > 0]
    win_rate = len(wins) / len(closed) if closed else 0.0
    avg_rr = (
        sum(float(r["risk_reward"] or 0) for r in rows) / len(rows) if rows else 0.0
    )
    return {
        "signals": signals,
        "waiting": waiting,
        "tp": tp,
        "sl": sl,
        "win_rate": round(win_rate, 3),
        "avg_rr": round(avg_rr, 2),
    }


def format_experimental_today_g39(conn: Any) -> str:
    s = experimental_stats_today_g39(conn)
    lines = [
        "Experimental today",
        "",
        "Signals",
        str(s["signals"]),
        "",
        "Waiting",
        str(s["waiting"]),
        "",
        "TP",
        str(s["tp"]),
        "",
        "SL",
        str(s["sl"]),
        "",
        "Win Rate",
        f"{s['win_rate'] * 100:.0f}%",
        "",
        "Average RR",
        f"{s['avg_rr']:.1f}",
    ]
    return "\n".join(lines)


def experimental_daily_section_g39(conn: Any, *, day_start: int, day_end: int) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT symbol, pnl_pct, risk_reward, result, status
        FROM {_TABLE}
        WHERE created_at BETWEEN ? AND ?
        """,
        (day_start, day_end),
    ).fetchall()
    closed = [r for r in rows if r["status"] == STATUS_CLOSED]
    wins = [r for r in closed if r["pnl_pct"] and float(r["pnl_pct"]) > 0]
    false_positives = [r for r in closed if r["pnl_pct"] and float(r["pnl_pct"]) <= 0]
    best = max(closed, key=lambda r: float(r["pnl_pct"] or -999)) if closed else None
    worst = min(closed, key=lambda r: float(r["pnl_pct"] or 999)) if closed else None
    return {
        "signals": len(rows),
        "win_rate": round(len(wins) / len(closed), 3) if closed else 0.0,
        "avg_rr": round(sum(float(r["risk_reward"] or 0) for r in rows) / len(rows), 2) if rows else 0.0,
        "best": f"{best['symbol']} {float(best['pnl_pct'] or 0):+.1f}%" if best else "—",
        "worst": f"{worst['symbol']} {float(worst['pnl_pct'] or 0):+.1f}%" if worst else "—",
        "false_positives": len(false_positives),
    }


def append_experimental_daily_report_g39(conn: Any, msg: str, report: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from bot.research.market_events.signal_intelligence.daily_report_g3 import _yesterday_bounds
    day_start, day_end, _ = _yesterday_bounds()
    exp = experimental_daily_section_g39(conn, day_start=day_start, day_end=day_end)
    report["experimental"] = exp
    extra = "\n".join([
        "",
        "Experimental",
        "",
        "Signals",
        str(exp["signals"]),
        "",
        "Win Rate",
        f"{exp['win_rate'] * 100:.0f}%",
        "",
        "Average RR",
        f"{exp['avg_rr']:.2f}",
        "",
        "Best",
        exp["best"],
        "",
        "Worst",
        exp["worst"],
        "",
        "False positives",
        str(exp["false_positives"]),
    ])
    return msg + extra, report


def replay_wr_comparison_g39(conn: Any, *, days: int = 7) -> dict[str, Any]:
    """Production WR vs Experimental WR from candidate replay outcomes."""
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        """
        SELECT o.would_hit_tp, o.max_profit_pct, o.max_drawdown_pct,
               c.confidence, c.market_score, c.rr, c.liquidity_score
        FROM market_candidate_outcomes_g32 o
        JOIN market_candidate_g31 c ON c.id = o.candidate_id
        WHERE o.created_at >= ? AND c.confidence IS NOT NULL
        """,
        (since,),
    ).fetchall()

    def _win(row: Any) -> bool:
        if row["would_hit_tp"]:
            return True
        return float(row["max_profit_pct"] or 0) > 0

    def _passes(row: Any, profile: ThresholdProfileG39) -> bool:
        conf = float(row["confidence"] or 0)
        ms = float(row["market_score"] or 0)
        liq = float(row["liquidity_score"] or 0) / 100.0
        rr = float(row["rr"] or 0)
        return (
            conf >= profile.min_confidence
            and ms >= profile.min_market_score
            and liq >= profile.min_liquidity_prob
            and rr >= profile.min_rr
        )

    prod = [r for r in rows if _passes(r, PRODUCTION_PROFILE)]
    exp = [r for r in rows if _passes(r, EXPERIMENTAL_PROFILE)]
    exp_only = [r for r in rows if _passes(r, EXPERIMENTAL_PROFILE) and not _passes(r, PRODUCTION_PROFILE)]

    def _wr(subset: list[Any]) -> float:
        if not subset:
            return 0.0
        return sum(1 for r in subset if _win(r)) / len(subset)

    span = max(1.0, days)
    return {
        "production_wr": round(_wr(prod), 3),
        "experimental_wr": round(_wr(exp), 3),
        "experimental_only_wr": round(_wr(exp_only), 3),
        "production_samples": len(prod),
        "experimental_samples": len(exp),
        "production_signals_per_day": round(len(prod) / span, 1),
        "experimental_signals_per_day": round(len(exp) / span, 1),
        "experimental_only_per_day": round(len(exp_only) / span, 1),
    }


def format_replay_wr_comparison_g39(conn: Any, *, days: int = 7) -> str:
    c = replay_wr_comparison_g39(conn, days=days)
    return "\n".join([
        f"Replay WR Comparison — last {days} days",
        "",
        "Production WR",
        f"{c['production_wr'] * 100:.0f}%",
        f"(n={c['production_samples']}, ~{c['production_signals_per_day']}/day)",
        "",
        "Experimental WR",
        f"{c['experimental_wr'] * 100:.0f}%",
        f"(n={c['experimental_samples']}, ~{c['experimental_signals_per_day']}/day)",
        "",
        "Experimental-only WR",
        f"{c['experimental_only_wr'] * 100:.0f}%",
        f"(~{c['experimental_only_per_day']}/day)",
    ])


def run_threshold_simulator_g39(conn: Any, *, days: int = 7) -> dict[str, Any]:
    comp = replay_wr_comparison_g39(conn, days=days)
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        """
        SELECT confidence, market_score, rr, liquidity_score, created_at
        FROM market_candidate_g31
        WHERE created_at >= ? AND confidence IS NOT NULL AND market_score IS NOT NULL
        """,
        (since,),
    ).fetchall()
    span = max(1.0, days)

    def _count(profile: ThresholdProfileG39) -> int:
        n = 0
        for r in rows:
            liq = float(r["liquidity_score"] or 0) / 100.0
            if (
                float(r["confidence"] or 0) >= profile.min_confidence
                and float(r["market_score"] or 0) >= profile.min_market_score
                and liq >= profile.min_liquidity_prob
                and float(r["rr"] or 0) >= profile.min_rr
            ):
                n += 1
        return n

    prod_n = _count(PRODUCTION_PROFILE)
    exp_n = _count(EXPERIMENTAL_PROFILE)
    exp_wr = comp["experimental_wr"] or comp["experimental_only_wr"]
    avg_rr_rows = conn.execute(
        """
        SELECT AVG(rr) AS avg_rr FROM market_candidate_g31
        WHERE created_at >= ? AND rr IS NOT NULL
        """,
        (since,),
    ).fetchone()
    avg_rr = float(avg_rr_rows["avg_rr"] or 0) if avg_rr_rows else 0.0

    safe = exp_wr >= comp["production_wr"] * 0.85 or exp_n > prod_n
    recommendation = (
        "Experimental thresholds look safe."
        if safe else "Review experimental thresholds before promoting."
    )

    return {
        "production_signals_per_day": round(prod_n / span, 1),
        "experimental_signals_per_day": round(exp_n / span, 1),
        "expected_wr": round(exp_wr, 3),
        "avg_rr": round(avg_rr, 2),
        "recommendation": recommendation,
        "comparison": comp,
    }


def format_threshold_simulator_g39(conn: Any, *, days: int = 7) -> str:
    sim = run_threshold_simulator_g39(conn, days=days)
    return "\n".join([
        "Threshold Simulator",
        "",
        "Production",
        f"{sim['production_signals_per_day']} signals/day",
        "",
        "Experimental",
        f"{sim['experimental_signals_per_day']} signals/day",
        "",
        "Expected WR",
        f"{sim['expected_wr'] * 100:.0f}%",
        "",
        "Average RR",
        f"{sim['avg_rr']:.1f}",
        "",
        "Recommendation",
        sim["recommendation"],
    ])


def experimental_dashboard_g39(conn: Any, *, limit: int = 50) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT id, signal_uuid, symbol, direction, confidence, market_score,
               liquidity_probability, risk_reward, reason, status, result,
               telegram_sent, entry, tp1, tp2, tp3, sl, pnl_pct, created_at
        FROM {_TABLE}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    today = experimental_stats_today_g39(conn)
    wr = replay_wr_comparison_g39(conn, days=7)
    return {
        "tab": "Experimental Signals",
        "today": today,
        "replay_wr": wr,
        "signals": [dict(r) for r in rows],
    }


def format_experimental_report_g39(conn: Any) -> str:
    parts = [
        format_experimental_today_g39(conn),
        "",
        "---",
        "",
        format_replay_wr_comparison_g39(conn, days=7),
        "",
        "---",
        "",
        format_threshold_simulator_g39(conn, days=7),
    ]
    return "\n".join(parts)
