"""Phase F.2 — professional trading intelligence report orchestrator."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.ai_analyst_v2 import (
    PROMPT_VERSION_F2,
    analyze_ai_v2,
    build_ai_input_json,
)
from bot.research.market_events.signal_intelligence.atr_expansion_f2 import analyze_atr_expansion
from bot.research.market_events.signal_intelligence.correlation_f2 import analyze_correlation
from bot.research.market_events.signal_intelligence.exchange_consensus_f2 import (
    CONFIDENCE_ADJ,
    compute_exchange_consensus,
    persist_exchange_contexts,
)
from bot.research.market_events.signal_intelligence.funding_oi_history_f2 import (
    analyze_funding_oi_history,
    persist_funding_oi_history,
)
from bot.research.market_events.signal_intelligence.historical_similarity_v2 import (
    search_historical_similarity_v2,
)
from bot.research.market_events.signal_intelligence.market_structure_f2 import analyze_market_structure
from bot.research.market_events.signal_intelligence.signal_report_f1 import (
    _entry_recommendation,
    build_signal_report_f1,
)
from bot.research.market_events.signal_intelligence.volume_intel_f2 import analyze_volume_intel


@dataclass(frozen=True)
class SignalReportF2:
    event_id: int
    exchange_consensus: str
    exchange_detail: list[dict[str, Any]]
    funding_regime: str
    oi_regime: str
    rvol_20: float
    rvol_100: float
    vwap_deviation_pct: float
    volume_label: str
    market_structure: dict[str, Any]
    market_structure_labels: list[str]
    atr_percentile: float
    atr_expansion: float
    atr_exhaustion: bool
    correlation_snapshot: dict[str, Any]
    correlation_verdict: str
    historical_count: int
    historical_reversal_count: int
    historical_reversal_rate: float
    historical_matches: list[dict[str, Any]]
    confidence_score: float
    confidence_breakdown: dict[str, float]
    reversal_probability: float
    entry_recommendation: str
    expected_target_pct: float
    expected_stop_pct: float
    ai_summary_v2_ru: str
    telegram_rendered: str


def _news_count(conn: Any, event_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_context WHERE event_id = ? AND context_type = 'NEWS'",
        (event_id,),
    ).fetchone()
    return int(row["n"] if row else 0)


def _apply_consensus_to_confidence(
    f1_score: float,
    f1_breakdown: dict[str, float],
    consensus: str,
) -> tuple[float, dict[str, float]]:
    breakdown = dict(f1_breakdown)
    adj = CONFIDENCE_ADJ.get(consensus, 0.0)
    breakdown["exchange_consensus"] = round(adj, 2)
    score = round(min(10.0, max(0.0, f1_score + adj)), 1)
    return score, breakdown


def build_signal_report_f2(conn: Any, event_id: int) -> SignalReportF2 | None:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None

    symbol = row["symbol"]
    shock_ret = float(row["return_pct"] or 0)
    window = int(row["trigger_window_seconds"] or 900)
    vol_z = abs(float(row["volume_zscore"] or 0))

    f1 = build_signal_report_f1(conn, event_id)
    if not f1:
        return None

    consensus = compute_exchange_consensus(
        conn, symbol=symbol, shock_return_pct=shock_ret, window_seconds=window,
    )
    try:
        persist_exchange_contexts(conn, event_id=event_id, symbol=symbol, consensus=consensus)
    except Exception:
        pass

    funding = analyze_funding_oi_history(
        conn, symbol=symbol, shock_return_pct=shock_ret, event_id=event_id,
    )
    try:
        persist_funding_oi_history(conn, event_id=event_id, result=funding)
    except Exception:
        pass

    volume = analyze_volume_intel(conn, symbol=symbol)
    structure = analyze_market_structure(conn, symbol=symbol, shock_return_pct=shock_ret)
    atr = analyze_atr_expansion(conn, symbol=symbol)
    correlation = analyze_correlation(conn, symbol=symbol, shock_return_pct=shock_ret)
    history = search_historical_similarity_v2(
        conn,
        symbol=symbol,
        event_id=event_id,
        shock_return_pct=shock_ret,
        volume_zscore=vol_z,
        structure_labels=structure.labels,
    )

    rev_rate = history.reversal_rate if history.count >= 3 else f1.historical_reversal_rate
    rev_prob = round(min(0.95, max(0.05, rev_rate * 0.65 + f1.reversal_probability * 0.35)), 2)

    confidence, breakdown = _apply_consensus_to_confidence(
        f1.confidence_score, f1.confidence_breakdown, consensus.consensus,
    )

    pending = conn.execute(
        "SELECT confirmed_reversal FROM market_events_pending_shocks WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    pending_rev = pending["confirmed_reversal"] if pending else None
    entry = _entry_recommendation(
        confidence=confidence, rev_prob=rev_prob, pending_reversal=pending_rev,
    )

    target = f1.expected_target_pct
    stop = f1.expected_stop_pct

    ai_input = build_ai_input_json(
        symbol=symbol,
        shock_return_pct=shock_ret,
        exchange_consensus=consensus.consensus,
        funding_regime=funding.funding_regime,
        oi_regime=funding.oi_regime,
        volume_label=volume.volume_label,
        atr_percentile=atr.atr_percentile,
        structure_labels=structure.labels,
        correlation_verdict=correlation.verdict,
        correlation_peers=correlation.peers,
        historical_count=history.count,
        historical_reversal_rate=rev_rate,
        news_count=_news_count(conn, event_id),
    )
    ai = analyze_ai_v2(ai_input)

    from bot.research.market_events.signal_intelligence.telegram_f2 import render_premium_report

    report_data = {
        "symbol": symbol,
        "shock_return_pct": shock_ret,
        "window_seconds": window,
        "confidence_score": confidence,
        "reversal_probability": rev_prob,
        "exchange_consensus": consensus.consensus,
        "volume_label": volume.volume_label,
        "atr_percentile": atr.atr_percentile,
        "funding_regime": funding.funding_regime,
        "oi_regime": funding.oi_regime,
        "correlation_verdict": correlation.verdict,
        "correlation_peers": correlation.peers,
        "historical_count": history.count,
        "historical_reversal_count": history.reversal_count,
        "entry_recommendation": entry,
        "expected_target_pct": target,
        "expected_stop_pct": stop,
        "ai_summary_v2_ru": ai.summary_ru,
        "structure_labels": structure.labels,
    }
    telegram = render_premium_report(conn, event_id=event_id, data=report_data)

    return SignalReportF2(
        event_id=event_id,
        exchange_consensus=consensus.consensus,
        exchange_detail=consensus.venues,
        funding_regime=funding.funding_regime,
        oi_regime=funding.oi_regime,
        rvol_20=volume.rvol_20,
        rvol_100=volume.rvol_100,
        vwap_deviation_pct=volume.vwap_deviation_pct,
        volume_label=volume.volume_label,
        market_structure=structure.detail,
        market_structure_labels=structure.labels,
        atr_percentile=atr.atr_percentile,
        atr_expansion=atr.atr_expansion,
        atr_exhaustion=atr.atr_exhaustion,
        correlation_snapshot={"peers": correlation.peers, "asset_class": correlation.asset_class},
        correlation_verdict=correlation.verdict,
        historical_count=history.count,
        historical_reversal_count=history.reversal_count,
        historical_reversal_rate=rev_rate,
        historical_matches=history.matches,
        confidence_score=confidence,
        confidence_breakdown=breakdown,
        reversal_probability=rev_prob,
        entry_recommendation=entry,
        expected_target_pct=target,
        expected_stop_pct=stop,
        ai_summary_v2_ru=ai.summary_ru,
        telegram_rendered=telegram,
    )


def persist_signal_report_f2(conn: Any, report: SignalReportF2) -> int:
    now = int(time.time())
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_signal_reports_f2 (
          event_id, exchange_consensus, exchange_detail_json,
          funding_regime, oi_regime, rvol_20, rvol_100, vwap_deviation_pct,
          volume_label, market_structure_json, market_structure_labels,
          atr_percentile, atr_expansion, atr_exhaustion,
          correlation_snapshot_json, correlation_verdict,
          historical_count, historical_reversal_count, historical_reversal_rate,
          historical_similarity_json, confidence_score, confidence_breakdown_json,
          reversal_probability, entry_recommendation, expected_target_pct,
          expected_stop_pct, ai_summary_v2_ru, telegram_rendered,
          prompt_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          exchange_consensus = excluded.exchange_consensus,
          exchange_detail_json = excluded.exchange_detail_json,
          funding_regime = excluded.funding_regime,
          oi_regime = excluded.oi_regime,
          rvol_20 = excluded.rvol_20,
          rvol_100 = excluded.rvol_100,
          vwap_deviation_pct = excluded.vwap_deviation_pct,
          volume_label = excluded.volume_label,
          market_structure_json = excluded.market_structure_json,
          market_structure_labels = excluded.market_structure_labels,
          atr_percentile = excluded.atr_percentile,
          atr_expansion = excluded.atr_expansion,
          atr_exhaustion = excluded.atr_exhaustion,
          correlation_snapshot_json = excluded.correlation_snapshot_json,
          correlation_verdict = excluded.correlation_verdict,
          historical_count = excluded.historical_count,
          historical_reversal_count = excluded.historical_reversal_count,
          historical_reversal_rate = excluded.historical_reversal_rate,
          historical_similarity_json = excluded.historical_similarity_json,
          confidence_score = excluded.confidence_score,
          confidence_breakdown_json = excluded.confidence_breakdown_json,
          reversal_probability = excluded.reversal_probability,
          entry_recommendation = excluded.entry_recommendation,
          expected_target_pct = excluded.expected_target_pct,
          expected_stop_pct = excluded.expected_stop_pct,
          ai_summary_v2_ru = excluded.ai_summary_v2_ru,
          telegram_rendered = excluded.telegram_rendered,
          prompt_version = excluded.prompt_version,
          created_at = excluded.created_at
        """,
        (
            report.event_id,
            report.exchange_consensus,
            json.dumps(report.exchange_detail, ensure_ascii=False),
            report.funding_regime,
            report.oi_regime,
            report.rvol_20,
            report.rvol_100,
            report.vwap_deviation_pct,
            report.volume_label,
            json.dumps(report.market_structure, ensure_ascii=False),
            json.dumps(report.market_structure_labels, ensure_ascii=False),
            report.atr_percentile,
            report.atr_expansion,
            1 if report.atr_exhaustion else 0,
            json.dumps(report.correlation_snapshot, ensure_ascii=False),
            report.correlation_verdict,
            report.historical_count,
            report.historical_reversal_count,
            report.historical_reversal_rate,
            json.dumps(report.historical_matches, ensure_ascii=False),
            report.confidence_score,
            json.dumps(report.confidence_breakdown),
            report.reversal_probability,
            report.entry_recommendation,
            report.expected_target_pct,
            report.expected_stop_pct,
            report.ai_summary_v2_ru,
            report.telegram_rendered,
            PROMPT_VERSION_F2,
            now,
        ),
    )


def load_signal_report_f2(conn: Any, event_id: int) -> SignalReportF2 | None:
    row = conn.execute(
        "SELECT * FROM market_events_signal_reports_f2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return SignalReportF2(
        event_id=int(row["event_id"]),
        exchange_consensus=str(row["exchange_consensus"]),
        exchange_detail=json.loads(row["exchange_detail_json"] or "[]"),
        funding_regime=str(row["funding_regime"] or ""),
        oi_regime=str(row["oi_regime"] or ""),
        rvol_20=float(row["rvol_20"] or 0),
        rvol_100=float(row["rvol_100"] or 0),
        vwap_deviation_pct=float(row["vwap_deviation_pct"] or 0),
        volume_label=str(row["volume_label"] or ""),
        market_structure=json.loads(row["market_structure_json"] or "{}"),
        market_structure_labels=json.loads(row["market_structure_labels"] or "[]"),
        atr_percentile=float(row["atr_percentile"] or 0),
        atr_expansion=float(row["atr_expansion"] or 0),
        atr_exhaustion=bool(row["atr_exhaustion"]),
        correlation_snapshot=json.loads(row["correlation_snapshot_json"] or "{}"),
        correlation_verdict=str(row["correlation_verdict"] or ""),
        historical_count=int(row["historical_count"] or 0),
        historical_reversal_count=int(row["historical_reversal_count"] or 0),
        historical_reversal_rate=float(row["historical_reversal_rate"] or 0),
        historical_matches=json.loads(row["historical_similarity_json"] or "[]"),
        confidence_score=float(row["confidence_score"]),
        confidence_breakdown=json.loads(row["confidence_breakdown_json"] or "{}"),
        reversal_probability=float(row["reversal_probability"]),
        entry_recommendation=str(row["entry_recommendation"]),
        expected_target_pct=float(row["expected_target_pct"]),
        expected_stop_pct=float(row["expected_stop_pct"]),
        ai_summary_v2_ru=str(row["ai_summary_v2_ru"] or ""),
        telegram_rendered=str(row["telegram_rendered"] or ""),
    )


def run_signal_report_f2(conn: Any, event_id: int) -> SignalReportF2 | None:
    report = build_signal_report_f2(conn, event_id)
    if report:
        persist_signal_report_f2(conn, report)
    return report
