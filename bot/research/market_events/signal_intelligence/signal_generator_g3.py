"""Phase G.3 — live signal generator with daily cap and strict gates."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import (
    G3_MAX_SIGNALS_PER_DAY,
    G3_MODEL_VERSION,
)
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    CandidateG31,
    STATE_CANDIDATE,
    mark_candidate_accepted_g31,
    pick_best_candidate_g31,
)
from bot.research.market_events.signal_intelligence.dominance_context_f7 import classify_dominance
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import LiquidityStateG3
from bot.research.market_events.signal_intelligence.reversal_learning_g1 import lookup_historical_reversal_rate
from bot.research.market_events.signal_intelligence.telegram_g3 import format_professional_telegram_g3
from bot.research.market_events.signal_intelligence.trade_geometry_s12 import (
    assert_sendable_geometry_s12,
    shock_direction_for_trade_side,
)
from bot.research.market_events.signal_intelligence.trade_plan_f71 import (
    compute_position_size_pct,
    compute_trade_plan_f71,
    resolve_event_price,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "ACTIVE"
STATUS_DASHBOARD = "DASHBOARD"


@dataclass(frozen=True)
class LiveSignalG3:
    signal_id: int
    signal_uuid: str
    symbol: str
    direction: str
    confidence: float
    probability: float
    market_score: float
    liquidity_state: str
    liquidity_probability: float
    risk_reward: float
    telegram_rendered: str
    dashboard_only: bool
    event_id: int | None = None


def _signals_sent_today(conn: Any, *, now_ts: int | None = None) -> int:
    ts = now_ts or int(time.time())
    day_start = ts - (ts % 86400)
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_live_signals_g3
        WHERE created_at >= ? AND telegram_sent = 1 AND dashboard_only = 0
        """,
        (day_start,),
    ).fetchone()
    return int(row["n"] or 0)


def _btc_conflicts(conn: Any, *, symbol: str, direction: str) -> bool:
    dom = classify_dominance(conn, shock_symbol=symbol)
    if direction == "LONG" and dom.regime == "RISK OFF":
        return True
    if direction == "SHORT" and dom.regime == "RISK ON" and dom.btc_return > 1.5:
        return True
    return False


def _best_trend(trends: list[TrendWindowG3], symbol: str) -> TrendWindowG3 | None:
    sym_trends = [t for t in trends if t.symbol == symbol]
    if not sym_trends:
        return None
    return max(sym_trends, key=lambda t: t.trend_score)


def _score_signal(
    *,
    trend: TrendWindowG3,
    liquidity: LiquidityStateG3,
    f5_conf: float | None,
    f7_score: float | None,
    f7_conf: float | None,
) -> tuple[float, float, float]:
    trend_score = trend.trend_score
    liq_prob = liquidity.probabilities.get(liquidity.primary_state, 0.1)
    confidence = f7_conf or f5_conf or min(10.0, 5.0 + trend_score / 20.0)
    market_score = f7_score or min(100.0, trend_score * 0.7 + liq_prob * 100 * 0.3)
    probability = min(0.95, liq_prob * 0.6 + (confidence / 10.0) * 0.4)
    return confidence, market_score, probability


def _build_reasons(liquidity: LiquidityStateG3, trend: TrendWindowG3) -> list[str]:
    reasons: list[str] = []
    if liquidity.factors.get("liquidations"):
        reasons.append("Liquidations")
    if liquidity.factors.get("funding") is not None:
        reasons.append("Funding")
    if liquidity.factors.get("oi_rising") is not None:
        reasons.append("OI")
    reasons.append("Historical match")
    if trend.pattern_type not in ("consecutive",):
        reasons.append(trend.pattern_type.replace("_", " ").title())
    return reasons


def evaluate_live_signal_g3(
    conn: Any,
    *,
    snapshot_id: int,
    trends: list[TrendWindowG3],
    liquidity: LiquidityStateG3,
    event_id: int | None = None,
    candidates: list[CandidateG31] | None = None,
) -> LiveSignalG3 | None:
    if candidates is None:
        from bot.research.market_events.signal_intelligence.candidate_g31 import build_candidates_g31
        candidates = build_candidates_g31(
            conn, snapshot_id=snapshot_id, trends=trends, liquidity=liquidity, event_id=event_id,
        )

    best_cand = pick_best_candidate_g31(candidates)
    if not best_cand or not best_cand.trend or not best_cand.direction:
        return None

    best_sym = best_cand.symbol
    best_trend = best_cand.trend
    direction = best_cand.direction
    confidence = float(best_cand.confidence or 0)
    market_score = float(best_cand.market_score or 0)
    liq_prob = float(best_cand.liquidity_score or 0) / 100.0
    probability = min(0.95, liq_prob * 0.6 + (confidence / 10.0) * 0.4)

    g2_summary = None
    if event_id:
        g2 = conn.execute(
            "SELECT summary_ru, reversal_probability FROM market_events_ai_research_g2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if g2 and g2["summary_ru"]:
            g2_summary = str(g2["summary_ru"])
    price = None
    if event_id:
        price = resolve_event_price(conn, event_id=event_id, symbol=best_sym)
    if not price:
        col = best_sym.lower()
        if col in ("btc", "eth", "sol", "bnb"):
            snap = conn.execute(
                f"SELECT {col}_price AS p FROM market_snapshots_g3 WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            if snap and snap["p"]:
                price = float(snap["p"])
        if not price:
            from bot.research.market_events.signal_intelligence.candles import load_recent_candles
            bars = load_recent_candles(conn, symbol=best_sym, venue="binance_futures", timeframe="5m", limit=2)
            if bars:
                price = float(bars[-1].close)

    from bot.research.market_events.signal_intelligence.risk_reward_f5 import (
        RiskRewardF5,
        compute_risk_reward_f5,
    )
    rr_obj = compute_risk_reward_f5(
        expected_target_pct=2.5,
        expected_stop_pct=1.0,
        reversal_probability=probability,
        dynamic_confidence=confidence,
        historical_reversal_rate=0.75,
    )
    trade_plan = compute_trade_plan_f71(
        price=price or 100.0,
        shock_direction=shock_direction_for_trade_side(direction),
        risk_reward=rr_obj,
        final_confidence=confidence,
    )
    geom = assert_sendable_geometry_s12(
        direction=direction,
        entry=trade_plan.entry,
        tp1=trade_plan.tp1,
        tp2=trade_plan.tp2,
        sl=trade_plan.sl,
        tp3=trade_plan.tp3,
        context=f"live {best_sym}",
    )
    if not geom.ok:
        return None

    dom = classify_dominance(conn, shock_symbol=best_sym)
    btc_context = dom.regime if dom else "Neutral"
    trend_summary = best_trend.details.get("description", f"{best_trend.consecutive_candles} candles")
    reasons = _build_reasons(liquidity, best_trend)

    pattern_key = f"{best_sym}|{best_trend.pattern_type}|{best_trend.window_minutes}m"
    hist_rate = lookup_historical_reversal_rate(conn, pattern_key) or 0.82
    rows = conn.execute(
        """
        SELECT pattern_key, reversal_rate FROM market_events_g1_pattern_stats
        WHERE pattern_key LIKE ? ORDER BY samples DESC LIMIT 3
        """,
        (f"{best_sym}%",),
    ).fetchall()
    historical = [
        {"symbol": best_sym, "pattern": r["pattern_key"], "reversal_rate": float(r["reversal_rate"])}
        for r in rows
    ]
    if not historical:
        historical = [{"symbol": best_sym, "pattern": best_trend.pattern_type, "reversal_rate": hist_rate}]

    sent_today = _signals_sent_today(conn)
    dashboard_only = sent_today >= G3_MAX_SIGNALS_PER_DAY

    signal_uuid = str(uuid.uuid4())
    trade_json = {
        "entry": trade_plan.entry,
        "sl": trade_plan.sl,
        "tp1": trade_plan.tp1,
        "tp2": trade_plan.tp2,
        "tp3": trade_plan.tp3,
        "risk_reward": trade_plan.risk_reward,
        "position_size_pct": trade_plan.position_size_pct,
    }
    telegram = format_professional_telegram_g3(
        symbol=best_sym,
        direction=direction,
        confidence=confidence,
        probability=probability,
        market_score=market_score,
        liquidity_state=liquidity.primary_state,
        trend_summary=str(trend_summary),
        funding=liquidity.factors.get("funding"),
        oi_rising=liquidity.factors.get("oi_rising"),
        btc_context=btc_context,
        reasons=reasons,
        trade_plan=trade_json,
        claude_summary=g2_summary,
        historical=historical,
        signal_uuid=signal_uuid,
        paper_mode=True,
        trend_coverage_pct=best_cand.trend_coverage_pct,
    )

    now = int(time.time())
    signal_id = insert_returning_id(
        conn,
        """
        INSERT INTO market_live_signals_g3 (
          signal_uuid, snapshot_id, event_id, symbol, direction, confidence, probability,
          market_score, liquidity_state, liquidity_probability, risk_reward, btc_context,
          trend_summary, reason_json, trade_plan_json, claude_summary, historical_json,
          telegram_rendered, telegram_sent, dashboard_only, status, position_size_pct,
          model_version, entry_price, tp1, tp2, tp3, sl, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_uuid, snapshot_id, event_id, best_sym, direction,
            confidence, probability, market_score, liquidity.primary_state, liq_prob,
            trade_plan.risk_reward, btc_context, str(trend_summary),
            json.dumps(reasons), json.dumps(trade_json), g2_summary, json.dumps(historical),
            telegram, 1 if dashboard_only else 0, STATUS_ACTIVE,
            trade_plan.position_size_pct, G3_MODEL_VERSION,
            trade_plan.entry, trade_plan.tp1, trade_plan.tp2, trade_plan.tp3, trade_plan.sl, now,
        ),
    )

    mark_candidate_accepted_g31(conn, snapshot_id=snapshot_id, symbol=best_sym)

    return LiveSignalG3(
        signal_id=signal_id,
        signal_uuid=signal_uuid,
        symbol=best_sym,
        direction=direction,
        confidence=confidence,
        probability=probability,
        market_score=market_score,
        liquidity_state=liquidity.primary_state,
        liquidity_probability=liq_prob,
        risk_reward=trade_plan.risk_reward,
        telegram_rendered=telegram,
        dashboard_only=dashboard_only,
        event_id=event_id,
    )


def send_live_signal_telegram_g3(conn: Any, signal: LiveSignalG3) -> bool:
    if signal.dashboard_only:
        logger.debug("g3 signal dashboard-only uuid=%s", signal.signal_uuid)
        return False
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert

    if not alert_shock_enabled():
        return False

    dedupe_key = f"g3-signal-{signal.signal_uuid}"
    ok = _safe_alert(
        conn,
        event_id=signal.event_id or 0,
        alert_type=ALERT_SHOCK,
        detail=dedupe_key,
        message=signal.telegram_rendered,
        enabled=True,
    )
    if ok:
        conn.execute(
            "UPDATE market_live_signals_g3 SET telegram_sent = 1 WHERE id = ?",
            (signal.signal_id,),
        )
    return ok


def run_g3_for_event(conn: Any, event_id: int) -> LiveSignalG3 | None:
    """Evaluate G3 signal from shock event context (additive hook)."""
    from bot.research.market_events.signal_intelligence.config import G3_ENABLED
    if not G3_ENABLED:
        return None

    existing = conn.execute(
        "SELECT id FROM market_live_signals_g3 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if existing:
        return None

    from bot.research.market_events.signal_intelligence.candidate_g31 import run_candidate_pipeline_g31
    from bot.research.market_events.signal_intelligence.recorder_g3 import record_market_snapshot_g3
    from bot.research.market_events.signal_intelligence.trend_windows_g3 import run_trend_detection_g3
    from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import (
        compute_liquidity_state_g3,
        persist_liquidity_state_g3,
    )
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols

    snapshot_id, _ = record_market_snapshot_g3(conn)
    symbols = load_g31_universe_symbols(conn)
    trends = run_trend_detection_g3(conn, snapshot_id=snapshot_id, symbols=symbols)
    liquidity = compute_liquidity_state_g3(conn, snapshot_id=snapshot_id)
    persist_liquidity_state_g3(conn, snapshot_id=snapshot_id, state=liquidity)

    candidates = run_candidate_pipeline_g31(
        conn, snapshot_id=snapshot_id, trends=trends, liquidity=liquidity, event_id=event_id,
    )
    signal = evaluate_live_signal_g3(
        conn, snapshot_id=snapshot_id, trends=trends, liquidity=liquidity,
        event_id=event_id, candidates=candidates,
    )
    if signal and not signal.dashboard_only:
        send_live_signal_telegram_g3(conn, signal)
    return signal


def format_g3_trace(conn: Any, event_id: int | None = None) -> str:
    if event_id:
        row = conn.execute(
            "SELECT * FROM market_live_signals_g3 WHERE event_id = ? ORDER BY id DESC LIMIT 1",
            (event_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM market_live_signals_g3 ORDER BY id DESC LIMIT 1",
        ).fetchone()
    if not row:
        return "G3 trace: no signals"
    lines = [
        f"G3 Signal {row['signal_uuid']}",
        f"Event {row['event_id'] or '—'}",
        f"{row['symbol']} {row['direction']}",
        f"Confidence {row['confidence']:.1f}",
        f"Market Score {row['market_score']:.0f}",
        f"Liquidity {row['liquidity_state']} ({float(row['liquidity_probability']) * 100:.0f}%)",
        f"Status {row['status']}",
        f"Telegram {'sent' if row['telegram_sent'] else 'pending'}",
    ]
    return "\n".join(lines)
