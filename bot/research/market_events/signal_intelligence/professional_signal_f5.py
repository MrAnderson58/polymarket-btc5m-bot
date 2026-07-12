"""Phase F.5 — Professional Signal Engine orchestrator."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.dynamic_confidence_f5 import compute_dynamic_confidence
from bot.research.market_events.signal_intelligence.entry_quality_f5 import grade_entry_quality
from bot.research.market_events.signal_intelligence.entry_stages_f3 import load_entry_stage
from bot.research.market_events.signal_intelligence.historical_examples_f5 import build_historical_examples
from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5, compute_risk_reward_f5
from bot.research.market_events.signal_intelligence.signal_reason_f5 import compose_signal_reasons
from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2
from bot.research.market_events.signal_intelligence.telegram_f5 import render_professional_telegram_f5
from bot.research.market_events.signal_intelligence.trend_shock_v2 import load_trend_shock_v2
from bot.research.market_events.signal_intelligence.visual_intel_f4 import load_visual_intel


@dataclass(frozen=True)
class ProfessionalSignalF5:
    event_id: int
    dynamic_confidence: float
    reversal_probability: float
    continuation_probability: float
    entry_quality: str
    signal_cause: str
    interest_factors: list[str]
    historical_examples: list[dict[str, Any]]
    risk_reward: RiskRewardF5
    priority_score: float
    telegram_eligible: bool
    telegram_skip_reason: str | None
    ai_summary_ru: str
    telegram_rendered: str


def _continuation_prob(trend: dict[str, Any] | None, report: Any) -> float:
    if trend:
        return float(trend.get("continuation_probability") or 0.5)
    if report:
        rev = report.reversal_probability
        rev = rev if rev <= 1.0 else rev / 100.0
        return round(1.0 - rev, 3)
    return 0.5


def build_professional_signal_f5(conn: Any, event_id: int) -> ProfessionalSignalF5 | None:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    report = load_signal_report_f2(conn, event_id)
    if not row or not report:
        return None

    trend = load_trend_shock_v2(conn, event_id)
    visual = load_visual_intel(conn, event_id)
    entry_stage_row = load_entry_stage(conn, event_id)
    entry_stage = entry_stage_row.stage if entry_stage_row else None

    fund = trend.get("funding") if trend else None
    funding_negative = fund is not None and float(fund) < 0
    oi_delta = trend.get("open_interest_delta") if trend else None
    oi_stalled = (
        (oi_delta is not None and float(oi_delta) <= 0)
        or report.oi_regime in ("falling", "divergence")
    )
    demand = bool(visual and (visual.get("chart_analysis") or {}).get("demand_zones"))
    stage = str(trend.get("stage") or "") if trend else ""
    support_lost = any("LL" in s or "Lower" in s for s in report.market_structure_labels)

    dynamic_conf, _bonuses, active = compute_dynamic_confidence(
        report.confidence_score,
        consecutive_bars=int(trend.get("consecutive_bars") or 0) if trend else 0,
        volume_multiple=float(trend.get("volume_multiple") or report.rvol_20 or 0) if trend else report.rvol_20,
        funding_negative=funding_negative,
        oi_stalled=oi_stalled,
        demand_zone=demand,
        historical_reversal_rate=report.historical_reversal_rate,
        trend_stage=stage,
        liquidation_signal=stage == "Capitulation",
        support_lost=support_lost,
    )

    from bot.research.market_events.signal_intelligence.trader_performance_f6 import (
        apply_author_confidence,
        load_trader_performance_for_event,
    )
    trader_perf = load_trader_performance_for_event(conn, event_id)
    dynamic_conf = apply_author_confidence(dynamic_conf, trader_perf)

    rev_prob = report.reversal_probability
    rev = rev_prob if rev_prob <= 1.0 else rev_prob / 100.0
    cont = _continuation_prob(trend, report)

    quality = grade_entry_quality(
        dynamic_confidence=dynamic_conf,
        reversal_probability=rev,
        entry_recommendation=report.entry_recommendation,
        historical_reversal_rate=report.historical_reversal_rate,
        entry_stage=entry_stage,
    )

    cause, checklist = compose_signal_reasons(
        trend=trend, report=report, visual=visual, active_factors=active,
    )
    examples = build_historical_examples(conn, matches=report.historical_matches)

    rr = compute_risk_reward_f5(
        expected_target_pct=report.expected_target_pct,
        expected_stop_pct=report.expected_stop_pct,
        reversal_probability=rev,
        dynamic_confidence=dynamic_conf,
        historical_reversal_rate=report.historical_reversal_rate,
    )

    priority_score = round(dynamic_conf * 10 + rev * 100 + report.historical_reversal_rate * 50, 2)

    from bot.research.market_events.signal_intelligence.config import (
        F5_MIN_TELEGRAM_CONFIDENCE,
        F5_PRIORITY_ENGINE,
    )
    from bot.research.market_events.signal_intelligence.priority_engine_f5 import should_send_f5_telegram

    if F5_PRIORITY_ENGINE:
        eligible, skip_reason = should_send_f5_telegram(
            conn,
            event_id=event_id,
            dynamic_confidence=dynamic_conf,
            priority_score=priority_score,
            min_confidence=F5_MIN_TELEGRAM_CONFIDENCE,
        )
    else:
        eligible = dynamic_conf >= F5_MIN_TELEGRAM_CONFIDENCE
        skip_reason = None if eligible else "low_confidence"

    ai = report.ai_summary_v2_ru or ""
    rendered = render_professional_telegram_f5(
        conn,
        event_id=event_id,
        dynamic_confidence=dynamic_conf,
        reversal_probability=rev,
        continuation_probability=cont,
        entry_quality=quality,
        signal_cause=cause,
        interest_factors=checklist,
        historical_examples=examples,
        risk_reward=rr,
        ai_summary=ai,
        trend=trend,
        report=report,
    )

    return ProfessionalSignalF5(
        event_id=event_id,
        dynamic_confidence=dynamic_conf,
        reversal_probability=round(rev, 3),
        continuation_probability=cont,
        entry_quality=quality,
        signal_cause=cause,
        interest_factors=checklist,
        historical_examples=examples,
        risk_reward=rr,
        priority_score=priority_score,
        telegram_eligible=eligible,
        telegram_skip_reason=None if eligible else skip_reason,
        ai_summary_ru=ai,
        telegram_rendered=rendered,
    )


def persist_professional_signal_f5(conn: Any, signal: ProfessionalSignalF5) -> None:
    rr = signal.risk_reward
    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_signal_reports_f5 (
          event_id, dynamic_confidence, reversal_probability, continuation_probability,
          entry_quality, signal_cause, interest_factors_json, historical_examples_json,
          risk_reward, tp_probabilities_json, tp1_pct, tp2_pct, tp3_pct, stop_pct,
          priority_score, telegram_eligible, telegram_skip_reason, ai_summary_ru,
          telegram_rendered, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          dynamic_confidence = excluded.dynamic_confidence,
          reversal_probability = excluded.reversal_probability,
          continuation_probability = excluded.continuation_probability,
          entry_quality = excluded.entry_quality,
          signal_cause = excluded.signal_cause,
          interest_factors_json = excluded.interest_factors_json,
          historical_examples_json = excluded.historical_examples_json,
          risk_reward = excluded.risk_reward,
          tp_probabilities_json = excluded.tp_probabilities_json,
          tp1_pct = excluded.tp1_pct,
          tp2_pct = excluded.tp2_pct,
          tp3_pct = excluded.tp3_pct,
          stop_pct = excluded.stop_pct,
          priority_score = excluded.priority_score,
          telegram_eligible = excluded.telegram_eligible,
          telegram_skip_reason = excluded.telegram_skip_reason,
          ai_summary_ru = excluded.ai_summary_ru,
          telegram_rendered = excluded.telegram_rendered,
          created_at = excluded.created_at
        """,
        (
            signal.event_id,
            signal.dynamic_confidence,
            signal.reversal_probability,
            signal.continuation_probability,
            signal.entry_quality,
            signal.signal_cause,
            json.dumps(signal.interest_factors, ensure_ascii=False),
            json.dumps(signal.historical_examples, ensure_ascii=False),
            rr.risk_reward,
            json.dumps({
                "tp1": rr.tp1_prob, "tp2": rr.tp2_prob, "tp3": rr.tp3_prob,
            }),
            rr.tp1_pct, rr.tp2_pct, rr.tp3_pct, rr.stop_pct,
            signal.priority_score,
            1 if signal.telegram_eligible else 0,
            signal.telegram_skip_reason,
            signal.ai_summary_ru,
            signal.telegram_rendered,
            int(time.time()),
        ),
    )


def load_professional_signal_f5(conn: Any, event_id: int) -> ProfessionalSignalF5 | None:
    row = conn.execute(
        "SELECT * FROM market_events_signal_reports_f5 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    tp = json.loads(row["tp_probabilities_json"] or "{}")
    rr = RiskRewardF5(
        risk_reward=float(row["risk_reward"]),
        tp1_prob=float(tp.get("tp1", 0)),
        tp2_prob=float(tp.get("tp2", 0)),
        tp3_prob=float(tp.get("tp3", 0)),
        tp1_pct=float(row["tp1_pct"]),
        tp2_pct=float(row["tp2_pct"]),
        tp3_pct=float(row["tp3_pct"]),
        stop_pct=float(row["stop_pct"]),
    )
    return ProfessionalSignalF5(
        event_id=int(row["event_id"]),
        dynamic_confidence=float(row["dynamic_confidence"]),
        reversal_probability=float(row["reversal_probability"]),
        continuation_probability=float(row["continuation_probability"]),
        entry_quality=str(row["entry_quality"]),
        signal_cause=str(row["signal_cause"]),
        interest_factors=json.loads(row["interest_factors_json"] or "[]"),
        historical_examples=json.loads(row["historical_examples_json"] or "[]"),
        risk_reward=rr,
        priority_score=float(row["priority_score"]),
        telegram_eligible=bool(int(row["telegram_eligible"])),
        telegram_skip_reason=row["telegram_skip_reason"],
        ai_summary_ru=str(row["ai_summary_ru"] or ""),
        telegram_rendered=str(row["telegram_rendered"]),
    )


def run_signal_engine_f5(conn: Any, event_id: int) -> ProfessionalSignalF5 | None:
    signal = build_professional_signal_f5(conn, event_id)
    if not signal:
        return None
    persist_professional_signal_f5(conn, signal)
    return signal
