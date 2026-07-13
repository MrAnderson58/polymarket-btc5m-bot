"""Phase F.7 — Market Intelligence Engine orchestrator."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import (
    F7_ENABLED,
    F7_MIN_FINAL_CONFIDENCE,
    F7_MIN_MARKET_SCORE,
)
from bot.research.market_events.signal_intelligence.dominance_context_f7 import classify_dominance
from bot.research.market_events.signal_intelligence.image_pipeline_f7 import analyze_image_pipeline
from bot.research.market_events.signal_intelligence.liquidation_intelligence_f7 import analyze_liquidations
from bot.research.market_events.signal_intelligence.long_trend_shock_f7 import analyze_long_trend_shock
from bot.research.market_events.signal_intelligence.market_score_f7 import (
    apply_market_score_multiplier,
    compute_market_score,
)
from bot.research.market_events.signal_intelligence.news_weight_f7 import analyze_news_weight
from bot.research.market_events.signal_intelligence.professional_signal_f5 import load_professional_signal_f5
from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2
from bot.research.market_events.signal_intelligence.telegram_f7 import (
    _human_factors,
    render_professional_telegram_f7,
)
from bot.research.market_events.signal_intelligence.trend_shock_v2 import load_trend_shock_v2
from bot.research.market_events.signal_intelligence.whale_activity_f7 import analyze_whale_activity


@dataclass(frozen=True)
class MarketIntelligenceF7:
    event_id: int
    market_score: float
    final_confidence: float
    success_probability: float
    dominance_regime: str
    liquidation_regime: str
    news_impact: str
    long_trend_stage: str | None
    telegram_rendered: str
    telegram_eligible: bool
    telegram_skip_reason: str | None


def _liq_to_dict(liq: Any) -> dict[str, Any]:
    return {
        "regime": liq.regime,
        "continuation_probability": liq.continuation_probability,
        "reversal_probability": liq.reversal_probability,
        "exchanges": liq.exchanges,
        "exhaustion": liq.exhaustion,
        "summary": liq.summary,
    }


def _dom_to_dict(dom: Any) -> dict[str, Any]:
    return {
        "regime": dom.regime,
        "btc_d_proxy": dom.btc_d_proxy,
        "eth_d_proxy": dom.eth_d_proxy,
        "total3_return": dom.total3_return,
        "btc_return": dom.btc_return,
        "eth_return": dom.eth_return,
        "summary": dom.summary,
    }


def _whale_to_dict(w: Any) -> dict[str, Any]:
    return {
        "score": w.score,
        "large_trades": w.large_trades,
        "oi_surge": w.oi_surge,
        "volume_spike": w.volume_spike,
        "cluster_detected": w.cluster_detected,
        "summary": w.summary,
    }


def _long_to_dict(lt: Any) -> dict[str, Any]:
    return {
        "primary_stage": lt.primary_stage,
        "primary_window": lt.primary_window,
        "cumulative_return_pct": lt.cumulative_return_pct,
        "direction": lt.direction,
        "hits": lt.hits,
        "summary": lt.summary,
    }


def _img_to_dict(img: Any) -> dict[str, Any]:
    return {
        "platform": img.platform,
        "has_image": img.has_image,
        "ticker": img.ticker,
        "timeframe": img.timeframe,
        "entry": img.entry,
        "tp_levels": img.tp_levels,
        "sl": img.sl,
        "direction": img.direction,
        "structure": img.structure,
        "zones": img.zones,
        "agreement_pct": img.agreement_pct,
        "agreement_text": img.agreement_text,
        "author_view": img.author_view,
        "divergence": img.divergence,
    }


def run_market_intel_f7(conn: Any, event_id: int) -> MarketIntelligenceF7 | None:
    if not F7_ENABLED:
        return None

    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None

    f5 = load_professional_signal_f5(conn, event_id)
    report = load_signal_report_f2(conn, event_id)
    trend = load_trend_shock_v2(conn, event_id)
    if not f5 or not report:
        return None

    symbol = str(row["symbol"])
    direction = str(row["direction"] or "DOWN")
    shock_return = float(row["return_pct"] or 0)
    event_ts = int(row["event_ts"])

    dominance = classify_dominance(conn, shock_symbol=symbol)
    liq = analyze_liquidations(conn, symbol=symbol, trend=trend, shock_return=shock_return)
    liq_dict = _liq_to_dict(liq)
    dom_dict = _dom_to_dict(dominance)

    market = compute_market_score(
        conn, report=report, trend=trend,
        dominance_regime=dominance.regime, liq_intel=liq_dict,
    )
    final_conf = apply_market_score_multiplier(f5.dynamic_confidence, market.score)

    news = analyze_news_weight(conn, event_id=event_id)
    if news.impact == "HIGH":
        final_conf = round(max(0.0, final_conf - 0.5), 1)
    elif news.impact == "MEDIUM":
        final_conf = round(max(0.0, final_conf - 0.2), 1)

    whale = analyze_whale_activity(conn, symbol=symbol, event_ts=event_ts)
    long_trend = analyze_long_trend_shock(conn, symbol=symbol)
    long_dict = long_trend and _long_to_dict(long_trend)

    image = analyze_image_pipeline(
        conn, event_id=event_id,
        shock_direction=direction,
        reversal_prob=f5.reversal_probability,
    )
    img_dict = image and _img_to_dict(image)

    success_prob = round(
        f5.reversal_probability * 0.5
        + liq.reversal_probability * 0.3
        + (market.score / 100.0) * 0.2,
        3,
    )

    factors = _human_factors(
        report=report,
        trend=trend,
        liq_intel=liq_dict,
        dominance=dom_dict,
        long_trend=long_dict,
        image_intel=img_dict,
        historical_rate=report.historical_reversal_rate,
    )

    rendered = render_professional_telegram_f7(
        conn,
        event_id=event_id,
        symbol=symbol,
        direction=direction,
        final_confidence=final_conf,
        success_probability=success_prob,
        market_score=market.score,
        risk_reward=f5.risk_reward,
        ai_summary=f5.ai_summary_ru,
        report=report,
        trend=trend,
        liq_intel=liq_dict,
        dominance=dom_dict,
        long_trend=long_dict,
        image_intel=img_dict,
        historical_rate=report.historical_reversal_rate,
    )

    eligible = final_conf >= F7_MIN_FINAL_CONFIDENCE and market.score >= F7_MIN_MARKET_SCORE
    skip_reason = None
    if final_conf < F7_MIN_FINAL_CONFIDENCE:
        skip_reason = "low_final_confidence"
    elif market.score < F7_MIN_MARKET_SCORE:
        skip_reason = "low_market_score"
    elif not f5.telegram_eligible:
        skip_reason = f5.telegram_skip_reason or "f5_filtered"

    intel = MarketIntelligenceF7(
        event_id=event_id,
        market_score=market.score,
        final_confidence=final_conf,
        success_probability=success_prob,
        dominance_regime=dominance.regime,
        liquidation_regime=liq.regime,
        news_impact=news.impact,
        long_trend_stage=long_trend.primary_stage if long_trend else None,
        telegram_rendered=rendered,
        telegram_eligible=eligible and f5.telegram_eligible,
        telegram_skip_reason=skip_reason,
    )

    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_market_intelligence_f7 (
          event_id, market_score, component_scores_json, final_confidence,
          success_probability, liquidation_regime, liquidation_continuation_prob,
          liquidation_reversal_prob, liquidation_intel_json, dominance_regime,
          dominance_json, whale_score, whale_intel_json, news_impact,
          news_keywords_json, long_trend_stage, long_trend_json,
          image_intel_json, interest_factors_json, telegram_rendered, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          market_score = excluded.market_score,
          component_scores_json = excluded.component_scores_json,
          final_confidence = excluded.final_confidence,
          success_probability = excluded.success_probability,
          liquidation_regime = excluded.liquidation_regime,
          liquidation_continuation_prob = excluded.liquidation_continuation_prob,
          liquidation_reversal_prob = excluded.liquidation_reversal_prob,
          liquidation_intel_json = excluded.liquidation_intel_json,
          dominance_regime = excluded.dominance_regime,
          dominance_json = excluded.dominance_json,
          whale_score = excluded.whale_score,
          whale_intel_json = excluded.whale_intel_json,
          news_impact = excluded.news_impact,
          news_keywords_json = excluded.news_keywords_json,
          long_trend_stage = excluded.long_trend_stage,
          long_trend_json = excluded.long_trend_json,
          image_intel_json = excluded.image_intel_json,
          interest_factors_json = excluded.interest_factors_json,
          telegram_rendered = excluded.telegram_rendered,
          created_at = excluded.created_at
        """,
        (
            intel.event_id,
            intel.market_score,
            json.dumps(market.components, ensure_ascii=False),
            intel.final_confidence,
            intel.success_probability,
            intel.liquidation_regime,
            liq.continuation_probability,
            liq.reversal_probability,
            json.dumps(liq_dict, ensure_ascii=False),
            intel.dominance_regime,
            json.dumps(dom_dict, ensure_ascii=False),
            whale.score,
            json.dumps(_whale_to_dict(whale), ensure_ascii=False),
            intel.news_impact,
            json.dumps(news.keywords, ensure_ascii=False),
            intel.long_trend_stage,
            json.dumps(long_dict or {}, ensure_ascii=False),
            json.dumps(img_dict or {}, ensure_ascii=False),
            json.dumps(factors, ensure_ascii=False),
            intel.telegram_rendered,
            int(time.time()),
        ),
    )
    return intel


def diagnose_f7_skip(conn: Any, event_id: int) -> str | None:
    """Return skip reason when F7 cannot run; None if prerequisites are met."""
    if not F7_ENABLED:
        return "disabled"
    row = conn.execute("SELECT 1 FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return "event not found"
    f5 = load_professional_signal_f5(conn, event_id)
    if not f5:
        return "F5 signal unavailable"
    report = load_signal_report_f2(conn, event_id)
    if not report:
        return "F2 report unavailable"
    return None


def run_f7_pipeline(conn: Any, event_id: int) -> MarketIntelligenceF7 | None:
    """Production F7 entry with explicit SUCCESS/SKIPPED trace."""
    from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
        record_f7_completed,
        record_f7_skipped,
    )

    reason = diagnose_f7_skip(conn, event_id)
    if reason:
        record_f7_skipped(conn, event_id=event_id, reason=reason)
        return None

    try:
        intel = run_market_intel_f7(conn, event_id)
    except Exception as exc:
        record_f7_skipped(conn, event_id=event_id, reason=str(exc) or exc.__class__.__name__)
        return None

    if not intel:
        record_f7_skipped(conn, event_id=event_id, reason="market score unavailable")
        return None

    record_f7_completed(
        conn,
        event_id=event_id,
        market_score=float(intel.market_score),
        final_confidence=float(intel.final_confidence),
    )
    return intel


def load_market_intelligence_f7(conn: Any, event_id: int) -> MarketIntelligenceF7 | None:
    row = conn.execute(
        "SELECT * FROM market_events_market_intelligence_f7 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return MarketIntelligenceF7(
        event_id=int(row["event_id"]),
        market_score=float(row["market_score"]),
        final_confidence=float(row["final_confidence"]),
        success_probability=float(row["success_probability"]),
        dominance_regime=str(row["dominance_regime"] or ""),
        liquidation_regime=str(row["liquidation_regime"] or ""),
        news_impact=str(row["news_impact"]),
        long_trend_stage=row["long_trend_stage"],
        telegram_rendered=str(row["telegram_rendered"]),
        telegram_eligible=True,
        telegram_skip_reason=None,
    )
