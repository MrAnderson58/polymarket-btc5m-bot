"""Phase F.1 — deterministic confidence score, explainability, entry recommendation."""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.confidence_f1 import (
    CONFIDENCE_WEIGHTS,
    ENTRY_READY,
    ENTRY_SCALE_IN_25,
    ENTRY_WAIT_R2,
    ENTRY_WAIT_R3,
    PROMPT_VERSION_F1,
)


@dataclass(frozen=True)
class SignalReportF1:
    event_id: int
    confidence_score: float
    confidence_breakdown: dict[str, float]
    reversal_probability: float
    explanation: dict[str, Any]
    entry_recommendation: str
    expected_target_pct: float
    expected_stop_pct: float
    matching_events: list[dict[str, Any]]
    historical_reversal_rate: float


def _fetch_similar_events(
    conn: Any,
    *,
    symbol: str,
    event_id: int,
    ret: float,
    limit: int = 8,
) -> list[dict[str, Any]]:
    threshold = max(1.0, abs(ret) * 0.55)
    rows = conn.execute(
        """
        SELECT e.id, e.event_ts, e.return_pct, e.direction, p.confirmed_reversal
        FROM market_events e
        LEFT JOIN market_events_pending_shocks p ON p.event_id = e.id
        WHERE e.symbol = ? AND e.id != ? AND ABS(e.return_pct) >= ?
        ORDER BY ABS(ABS(e.return_pct) - ?) ASC, e.event_ts DESC
        LIMIT ?
        """,
        (symbol, event_id, threshold, abs(ret), limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        pnl_row = conn.execute(
            """
            SELECT net_return FROM paper_strategy_runs
            WHERE event_id = ? AND net_return IS NOT NULL LIMIT 1
            """,
            (int(r["id"]),),
        ).fetchone()
        out.append({
            "event_id": int(r["id"]),
            "event_ts": int(r["event_ts"]),
            "return_pct": round(float(r["return_pct"] or 0), 2),
            "direction": r["direction"],
            "confirmed_reversal": r["confirmed_reversal"],
            "paper_pnl": float(pnl_row["net_return"]) if pnl_row else None,
        })
    return out


def _historical_reversal_rate(events: list[dict[str, Any]]) -> float:
    if not events:
        return 0.5
    revs = sum(1 for e in events if e.get("confirmed_reversal"))
    return revs / len(events)


def _component_scores(conn: Any, *, event_id: int, row: Any, similar: list[dict[str, Any]]) -> dict[str, float]:
    ret = abs(float(row["return_pct"] or 0))
    vol_z = abs(float(row["volume_zscore"] or 0))
    hist_rate = _historical_reversal_rate(similar)
    sim_n = min(len(similar) / 8.0, 1.0)

    exch = conn.execute(
        "SELECT funding, open_interest, atr FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()

    ctx_n = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_context WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    tg = min(int(ctx_n["n"] if ctx_n else 0) / 3.0, 1.0)

    news_n = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_context WHERE event_id = ? AND context_type = 'NEWS'",
        (event_id,),
    ).fetchone()
    news = 1.0 if int(news_n["n"] if news_n else 0) > 0 else 0.35

    funding_score = 0.0
    if exch and exch["funding"] is not None:
        funding_score = min(abs(float(exch["funding"])) * 15.0, 1.0)

    oi_score = 0.0
    if exch and exch["open_interest"] is not None and float(exch["open_interest"]) > 0:
        oi_score = 0.65

    atr_score = 0.0
    if exch and exch["atr"] is not None and ret > 0:
        price_proxy = ret  # relative scale
        atr_score = min(float(exch["atr"]) / max(price_proxy, 0.5), 1.0)

    ai_score = 0.5
    ai = conn.execute(
        """
        SELECT bias, reversal_probability FROM market_event_ai_analyses_f0
        WHERE event_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not ai:
        ai = conn.execute(
            """
            SELECT reversal_bias, confidence FROM market_event_ai_analyses
            WHERE event_id = ? ORDER BY created_at DESC LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        if ai and ai["reversal_bias"] == "FADE_FAVORED":
            ai_score = min(float(ai["confidence"] or 0.5), 1.0)
        elif ai and ai["reversal_bias"] == "CONTINUATION_FAVORED":
            ai_score = 0.25
    elif ai:
        if str(ai["bias"]).upper() in ("FADE", "WAIT"):
            ai_score = min(float(ai["reversal_probability"] or 0.5), 1.0)
        else:
            ai_score = 0.3

    return {
        "historical_similarity": round(min(1.0, sim_n * 0.6 + hist_rate * 0.4), 3),
        "volatility": round(min(ret / 6.0, 1.0), 3),
        "atr": round(atr_score if atr_score else min(ret / 5.0, 1.0), 3),
        "volume": round(min(vol_z / 3.0, 1.0), 3),
        "funding": round(funding_score, 3),
        "open_interest": round(oi_score, 3),
        "telegram_context": round(tg, 3),
        "news": round(news, 3),
        "ai_agreement": round(ai_score, 3),
    }


def compute_confidence(components: dict[str, float]) -> tuple[float, dict[str, float]]:
    breakdown: dict[str, float] = {}
    total = 0.0
    for key, weight in CONFIDENCE_WEIGHTS.items():
        raw = float(components.get(key, 0.0))
        pts = round(raw * weight, 2)
        breakdown[key] = pts
        total += pts
    return round(min(10.0, total), 1), breakdown


def _expected_move(similar: list[dict[str, Any]], shock_ret: float, rev_rate: float) -> tuple[float, float]:
    pnls = [abs(float(e["paper_pnl"])) for e in similar if e.get("paper_pnl") is not None and e["paper_pnl"]]
    if pnls:
        target = statistics.median(pnls)
    else:
        target = abs(shock_ret) * rev_rate * 0.45
    target = round(max(0.4, min(target, abs(shock_ret) * 0.8)), 2)
    stop = round(max(0.5, target * 0.45), 2)
    return target, stop


def _entry_recommendation(
    *,
    confidence: float,
    rev_prob: float,
    pending_reversal: str | None,
) -> str:
    rev = (pending_reversal or "").upper()
    has_r2 = "R2" in rev or rev in ("R3", "R4", "R5")
    has_r3 = "R3" in rev or rev in ("R4", "R5")

    if confidence >= 7.5 and rev_prob >= 0.62 and has_r2:
        return ENTRY_READY
    if confidence >= 6.0 and rev_prob >= 0.55 and has_r2:
        return ENTRY_SCALE_IN_25
    if confidence < 4.5 or rev_prob < 0.4:
        return ENTRY_WAIT_R3
    if not has_r2:
        return ENTRY_WAIT_R2
    if has_r3 and confidence >= 5.5:
        return ENTRY_SCALE_IN_25
    return ENTRY_WAIT_R2


def _build_explanation(
    *,
    components: dict[str, float],
    breakdown: dict[str, float],
    confidence: float,
    similar: list[dict[str, Any]],
    rev_rate: float,
    expected_target: float,
    entry: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    if rev_rate >= 0.65:
        reasons.append(f"История: {int(rev_rate * 100)}% разворотов ({len(similar)} похожих кейсов)")
    elif rev_rate <= 0.35:
        reasons.append(f"История слабая: только {int(rev_rate * 100)}% разворотов")
    else:
        reasons.append(f"История нейтральная: {int(rev_rate * 100)}% разворотов")

    if components.get("volatility", 0) >= 0.7:
        reasons.append("Волатильность повышена")
    elif components.get("volatility", 0) <= 0.35:
        reasons.append("Импульс умеренный")

    if breakdown.get("funding", 0) >= 0.5:
        reasons.append("Funding elevated — перегрев позиций")
    if breakdown.get("telegram_context", 0) >= 0.6:
        reasons.append("Telegram: есть контекст по активу")
    if breakdown.get("news", 0) >= 0.7:
        reasons.append("News: есть катализатор")
    if breakdown.get("ai_agreement", 0) >= 0.65:
        reasons.append("AI согласен с fade-сценарием")
    elif breakdown.get("ai_agreement", 0) <= 0.35:
        reasons.append("AI склоняется к continuation")

    if confidence >= 7:
        reasons.append("Уверенность высокая — сетап качественный")
    elif confidence <= 4:
        reasons.append("Уверенность низкая — лучше дождаться подтверждения")

    return {
        "reasons_ru": reasons,
        "why_confidence": "high" if confidence >= 7 else "low" if confidence <= 4 else "medium",
        "expected_move_pct": expected_target,
        "entry_plan": entry,
        "matching_count": len(similar),
    }


def build_signal_report_f1(conn: Any, event_id: int) -> SignalReportF1 | None:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None

    similar = _fetch_similar_events(
        conn, symbol=row["symbol"], event_id=event_id, ret=float(row["return_pct"] or 0),
    )
    rev_rate = _historical_reversal_rate(similar)
    components = _component_scores(conn, event_id=event_id, row=row, similar=similar)
    confidence, breakdown = compute_confidence(components)

    pending = conn.execute(
        "SELECT confirmed_reversal FROM market_events_pending_shocks WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    pending_rev = pending["confirmed_reversal"] if pending else None

    rev_prob = round(min(0.95, max(0.05, rev_rate * 0.7 + components["ai_agreement"] * 0.3)), 2)
    target, stop = _expected_move(similar, float(row["return_pct"] or 0), rev_rate)
    entry = _entry_recommendation(
        confidence=confidence, rev_prob=rev_prob, pending_reversal=pending_rev,
    )
    explanation = _build_explanation(
        components=components,
        breakdown=breakdown,
        confidence=confidence,
        similar=similar,
        rev_rate=rev_rate,
        expected_target=target,
        entry=entry,
    )

    return SignalReportF1(
        event_id=event_id,
        confidence_score=confidence,
        confidence_breakdown=breakdown,
        reversal_probability=rev_prob,
        explanation=explanation,
        entry_recommendation=entry,
        expected_target_pct=target,
        expected_stop_pct=stop,
        matching_events=similar[:5],
        historical_reversal_rate=round(rev_rate, 3),
    )


def persist_signal_report_f1(conn: Any, report: SignalReportF1) -> int:
    now = int(time.time())
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_signal_reports_f1 (
          event_id, confidence_score, confidence_breakdown_json,
          reversal_probability, explanation_json, entry_recommendation,
          expected_target_pct, expected_stop_pct, matching_events_json,
          historical_reversal_rate, prompt_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          confidence_score = excluded.confidence_score,
          confidence_breakdown_json = excluded.confidence_breakdown_json,
          reversal_probability = excluded.reversal_probability,
          explanation_json = excluded.explanation_json,
          entry_recommendation = excluded.entry_recommendation,
          expected_target_pct = excluded.expected_target_pct,
          expected_stop_pct = excluded.expected_stop_pct,
          matching_events_json = excluded.matching_events_json,
          historical_reversal_rate = excluded.historical_reversal_rate,
          prompt_version = excluded.prompt_version,
          created_at = excluded.created_at
        """,
        (
            report.event_id,
            report.confidence_score,
            json.dumps(report.confidence_breakdown),
            report.reversal_probability,
            json.dumps(report.explanation, ensure_ascii=False),
            report.entry_recommendation,
            report.expected_target_pct,
            report.expected_stop_pct,
            json.dumps(report.matching_events, ensure_ascii=False),
            report.historical_reversal_rate,
            PROMPT_VERSION_F1,
            now,
        ),
    )


def load_signal_report_f1(conn: Any, event_id: int) -> SignalReportF1 | None:
    row = conn.execute(
        "SELECT * FROM market_events_signal_reports_f1 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return SignalReportF1(
        event_id=int(row["event_id"]),
        confidence_score=float(row["confidence_score"]),
        confidence_breakdown=json.loads(row["confidence_breakdown_json"] or "{}"),
        reversal_probability=float(row["reversal_probability"]),
        explanation=json.loads(row["explanation_json"] or "{}"),
        entry_recommendation=str(row["entry_recommendation"]),
        expected_target_pct=float(row["expected_target_pct"]),
        expected_stop_pct=float(row["expected_stop_pct"]),
        matching_events=json.loads(row["matching_events_json"] or "[]"),
        historical_reversal_rate=float(row["historical_reversal_rate"]),
    )


def run_signal_report_f1(conn: Any, event_id: int) -> SignalReportF1 | None:
    report = build_signal_report_f1(conn, event_id)
    if report:
        persist_signal_report_f1(conn, report)
    return report
