"""Phase G.2 — Claude Research Agent (explanation only, no trading decisions)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.ai_context_f0 import build_f0_context_bundle
from bot.research.market_events.signal_intelligence.config import (
    G2_ENABLED,
    G2_MIN_CONFIDENCE,
    G2_MIN_MARKET_SCORE,
    PROMPT_VERSION_G2,
)
from bot.research.market_events.signal_intelligence.liquidity_trend_g1 import load_liquidity_trend_g1
from bot.research.market_events.signal_intelligence.telegram_g2 import format_research_telegram_g2
from bot.research.market_events.signal_intelligence.visual_analysis_g2 import run_visual_analysis_g2

logger = logging.getLogger(__name__)

_TABLE = "market_events_ai_research_g2"

_G2_JSON_SCHEMA = {
    "market_story": "string — why market reached this point",
    "bullish_factors": ["short bullets for reversal case"],
    "bearish_factors": ["short bullets"],
    "reversal_probability": "0-1 number (research estimate, does not change engine)",
    "continuation_probability": "0-1 number",
    "risks": ["continuation / risk bullets"],
    "invalidates": ["what invalidates the thesis"],
    "summary_ru": "one line conclusion e.g. Ждать подтверждения R2.",
}

_SYSTEM_PROMPT = """You are a market research analyst (Claude Sonnet).
Explain market structure only. You do NOT place trades, open positions,
or modify confidence scores used by the deterministic trading engine.
Answer in Russian inside JSON fields. Output ONLY valid JSON matching the schema."""


@dataclass(frozen=True)
class ResearchG2:
    event_id: int
    symbol: str
    market_story: str
    bullish_factors: list[str]
    bearish_factors: list[str]
    reversal_probability: float
    continuation_probability: float
    risks: list[str]
    invalidates: list[str]
    summary_ru: str
    telegram_block: str
    provider: str
    confidence: float
    market_score: float


def check_g2_eligibility(conn: Any, event_id: int) -> tuple[bool, str]:
    """Gate: F5+F7+G1 present, confidence≥7, market score≥60, research pipeline only."""
    if not G2_ENABLED:
        return False, "disabled"

    from bot.research.market_events.signal_intelligence.market_intel_f7 import load_market_intelligence_f7
    from bot.research.market_events.signal_intelligence.professional_signal_f5 import load_professional_signal_f5

    f5 = load_professional_signal_f5(conn, event_id)
    if not f5:
        return False, "no_f5"
    f7 = load_market_intelligence_f7(conn, event_id)
    if not f7:
        return False, "no_f7"
    g1 = load_liquidity_trend_g1(conn, event_id)
    if not g1:
        return False, "no_g1"

    if float(f7.final_confidence) < G2_MIN_CONFIDENCE:
        return False, "low_confidence"
    if float(f7.market_score) < G2_MIN_MARKET_SCORE:
        return False, "low_market_score"
    return True, "ok"


def build_g2_context(conn: Any, event_id: int) -> dict[str, Any] | None:
    g1_row = conn.execute(
        "SELECT * FROM market_events_liquidity_trend_g1 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not g1_row:
        return None

    from bot.research.market_events.signal_intelligence.market_intel_f7 import load_market_intelligence_f7
    from bot.research.market_events.signal_intelligence.professional_signal_f5 import load_professional_signal_f5

    f5 = load_professional_signal_f5(conn, event_id)
    f7 = load_market_intelligence_f7(conn, event_id)
    bundle = build_f0_context_bundle(conn, event_id)

    f2 = conn.execute(
        "SELECT funding_regime, oi_regime, correlation_verdict, historical_reversal_rate "
        "FROM market_events_signal_reports_f2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    trend = conn.execute(
        "SELECT stage, funding, open_interest_delta, volume_multiple, consecutive_bars "
        "FROM market_events_trend_shock_v2 WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()

    return {
        "event_id": event_id,
        "symbol": g1_row["symbol"],
        "mode": "paper_research_only",
        "f5": {
            "dynamic_confidence": f5.dynamic_confidence if f5 else None,
            "reversal_probability": f5.reversal_probability if f5 else None,
            "signal_cause": f5.signal_cause if f5 else None,
        },
        "f7": {
            "final_confidence": f7.final_confidence if f7 else None,
            "market_score": f7.market_score if f7 else None,
            "dominance_regime": f7.dominance_regime if f7 else None,
            "liquidation_regime": f7.liquidation_regime if f7 else None,
        },
        "g1": {
            "signal_type": g1_row["signal_type"],
            "reversal_probability": float(g1_row["reversal_probability"]),
            "continuation_probability": float(g1_row["continuation_probability"]),
            "mtf_windows": json.loads(g1_row["mtf_windows_json"] or "[]"),
            "consecutive_patterns": json.loads(g1_row["consecutive_pattern_json"] or "[]"),
            "slow_trend": json.loads(g1_row["slow_trend_json"]) if g1_row["slow_trend_json"] else None,
            "liquidity_accum": json.loads(g1_row["liquidity_accum_json"]) if g1_row["liquidity_accum_json"] else None,
            "capitulation": json.loads(g1_row["capitulation_json"]) if g1_row["capitulation_json"] else None,
        },
        "market_event": bundle.get("market_event"),
        "f2": dict(f2) if f2 else None,
        "trend_v2": dict(trend) if trend else None,
    }


def build_g2_prompt(ctx: dict[str, Any]) -> str:
    return json.dumps({
        "required_schema": _G2_JSON_SCHEMA,
        "context": ctx,
    }, ensure_ascii=False, default=str)


def _parse_g2_response(data: dict[str, Any]) -> dict[str, Any] | None:
    required = (
        "market_story", "bullish_factors", "bearish_factors",
        "reversal_probability", "continuation_probability",
        "risks", "invalidates", "summary_ru",
    )
    for key in required:
        if key not in data:
            return None
    rev = float(data["reversal_probability"])
    cont = float(data["continuation_probability"])
    return {
        "market_story": str(data["market_story"])[:800],
        "bullish_factors": [str(x) for x in data.get("bullish_factors") or []][:5],
        "bearish_factors": [str(x) for x in data.get("bearish_factors") or []][:5],
        "reversal_probability": round(min(1.0, max(0.0, rev)), 3),
        "continuation_probability": round(min(1.0, max(0.0, cont)), 3),
        "risks": [str(x) for x in data.get("risks") or []][:5],
        "invalidates": [str(x) for x in data.get("invalidates") or []][:5],
        "summary_ru": str(data["summary_ru"])[:200],
    }


def analyze_research_g2_deterministic(ctx: dict[str, Any]) -> dict[str, Any]:
    """Fallback when Claude unavailable — does not touch engine confidence."""
    g1 = ctx.get("g1") or {}
    f2 = ctx.get("f2") or {}
    trend = ctx.get("trend_v2") or {}
    me = ctx.get("market_event") or {}
    f7 = ctx.get("f7") or {}

    story_parts: list[str] = []
    if g1.get("capitulation"):
        story_parts.append("Фаза капитуляции с всплеском объёма")
    if g1.get("slow_trend"):
        story_parts.append(g1["slow_trend"].get("description", "Медленный тренд"))
    if g1.get("liquidity_accum"):
        story_parts.append("Накопление ликвидности при падении")
    patterns = g1.get("consecutive_patterns") or []
    if patterns:
        best = max(patterns, key=lambda p: p.get("max_streak", 0))
        story_parts.append(best.get("description", ""))
    if not story_parts:
        story_parts.append(f"Импульс {float(me.get('return_pct') or 0):+.1f}%")

    reversal_factors: list[str] = []
    if g1.get("liquidity_accum"):
        liq = g1["liquidity_accum"]
        if liq.get("funding_negative"):
            reversal_factors.append("отрицательный Funding")
        if liq.get("oi_rising"):
            reversal_factors.append("рост OI")
    if g1.get("capitulation"):
        reversal_factors.append("капитуляция")
    if trend.get("stage") == "Capitulation":
        reversal_factors.append("стадия Capitulation")
    if not reversal_factors:
        reversal_factors.append("признаки истощения")

    risks: list[str] = []
    if f2.get("correlation_verdict") in ("against", "btc_against"):
        risks.append("BTC остаётся слабым")
    cap = g1.get("capitulation") or {}
    if cap and float(cap.get("liquidation_intensity") or 0) < 0.5:
        risks.append("ликвидации не завершились")
    if not risks:
        risks.append("импульс может продолжиться")

    rev_p = float(g1.get("reversal_probability") or 0.55)
    cont_p = float(g1.get("continuation_probability") or 1 - rev_p)

    return {
        "market_story": ". ".join(p for p in story_parts if p)[:500],
        "bullish_factors": reversal_factors,
        "bearish_factors": [],
        "reversal_probability": rev_p,
        "continuation_probability": cont_p,
        "risks": risks,
        "invalidates": ["пробой экстремума без R2", "ускорение funding против отката"],
        "summary_ru": "Ждать подтверждения R2.",
        "_meta_confidence": float(f7.get("final_confidence") or 0),
        "_meta_market_score": float(f7.get("market_score") or 0),
    }


def _call_claude_research(ctx: dict[str, Any]) -> tuple[dict[str, Any], str, str, int, int, float, float]:
    in_tok = out_tok = 0
    cost = 0.0
    latency = 0.0

    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            call_claude_json_g2,
            is_claude_configured,
        )
        if is_claude_configured():
            parsed, resp = call_claude_json_g2(
                system=_SYSTEM_PROMPT,
                prompt=build_g2_prompt(ctx),
                label="g2_research",
            )
            result = _parse_g2_response(parsed)
            if result:
                return (
                    result, "anthropic", resp.model,
                    resp.usage.input_tokens, resp.usage.output_tokens,
                    resp.usage.cost_usd, resp.usage.latency_ms,
                )
    except Exception as exc:
        logger.debug("g2 claude research failed: %s", exc)

    fallback = analyze_research_g2_deterministic(ctx)
    return fallback, "deterministic", "g2_rules_v1", in_tok, out_tok, cost, latency


def run_claude_research_g2(conn: Any, event_id: int) -> ResearchG2 | None:
    """Research-only second opinion. Never changes confidence or opens trades."""
    ok, reason = check_g2_eligibility(conn, event_id)
    if not ok:
        logger.debug("g2 skipped event=%s reason=%s", event_id, reason)
        return None

    existing = conn.execute(f"SELECT 1 FROM {_TABLE} WHERE event_id = ?", (event_id,)).fetchone()
    if existing:
        return load_research_g2(conn, event_id)

    ctx = build_g2_context(conn, event_id)
    if not ctx:
        return None

    result, provider, model, in_tok, out_tok, cost, latency = _call_claude_research(ctx)

    visual = run_visual_analysis_g2(
        conn, event_id,
        shock_context={
            "symbol": ctx["symbol"],
            "g1": ctx["g1"],
            "market_event": ctx["market_event"],
        },
    )
    if visual and visual.get("summary_ru"):
        result["market_story"] = (result["market_story"] + " " + visual["summary_ru"])[:800]

    f7 = ctx["f7"] or {}
    conf = float(f7.get("final_confidence") or 0)
    mscore = float(f7.get("market_score") or 0)
    g1 = load_liquidity_trend_g1(conn, event_id)
    assert g1 is not None

    telegram = format_research_telegram_g2(
        reversal_factors=result["bullish_factors"],
        risks=result["risks"],
        summary_ru=result["summary_ru"],
    )
    now = int(time.time())

    # Legacy columns kept for backward compatibility
    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_TABLE} (
          event_id, symbol, g1_signal_type, g1_reversal_probability,
          why_at_this_point, reversal_signs_json, continuation_signs_json,
          invalidation_json, conclusion, telegram_block,
          market_story, bullish_factors_json, bearish_factors_json,
          reversal_probability, continuation_probability,
          risks_json, invalidates_json, summary_ru,
          confidence, market_score, input_tokens, output_tokens, cost_usd,
          provider, model, prompt_version, response_json, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, g1.symbol, g1.signal_type, g1.reversal_probability,
            result["market_story"],
            json.dumps(result["bullish_factors"], ensure_ascii=False),
            json.dumps(result["risks"], ensure_ascii=False),
            json.dumps(result["invalidates"], ensure_ascii=False),
            result["summary_ru"], telegram,
            result["market_story"],
            json.dumps(result["bullish_factors"], ensure_ascii=False),
            json.dumps(result["bearish_factors"], ensure_ascii=False),
            result["reversal_probability"], result["continuation_probability"],
            json.dumps(result["risks"], ensure_ascii=False),
            json.dumps(result["invalidates"], ensure_ascii=False),
            result["summary_ru"],
            conf, mscore, in_tok, out_tok, cost,
            provider, model, PROMPT_VERSION_G2,
            json.dumps({**result, "visual": visual}, ensure_ascii=False),
            latency, now,
        ),
    )

    return ResearchG2(
        event_id=event_id,
        symbol=g1.symbol,
        market_story=result["market_story"],
        bullish_factors=result["bullish_factors"],
        bearish_factors=result["bearish_factors"],
        reversal_probability=result["reversal_probability"],
        continuation_probability=result["continuation_probability"],
        risks=result["risks"],
        invalidates=result["invalidates"],
        summary_ru=result["summary_ru"],
        telegram_block=telegram,
        provider=provider,
        confidence=conf,
        market_score=mscore,
    )


def load_research_g2(conn: Any, event_id: int) -> ResearchG2 | None:
    row = conn.execute(f"SELECT * FROM {_TABLE} WHERE event_id = ?", (event_id,)).fetchone()
    if not row:
        return None
    cols = set(row.keys())
    bullish = json.loads(
        row["bullish_factors_json"] if "bullish_factors_json" in cols and row["bullish_factors_json"]
        else row["reversal_signs_json"] or "[]",
    )
    risks = json.loads(
        row["risks_json"] if "risks_json" in cols and row["risks_json"]
        else row["continuation_signs_json"] or "[]",
    )
    invalidates = json.loads(
        row["invalidates_json"] if "invalidates_json" in cols and row["invalidates_json"]
        else row["invalidation_json"] or "[]",
    )
    market_story = row["market_story"] if "market_story" in cols and row["market_story"] else row["why_at_this_point"]
    summary = row["summary_ru"] if "summary_ru" in cols and row["summary_ru"] else row["conclusion"]
    rev_p = row["reversal_probability"] if "reversal_probability" in cols and row["reversal_probability"] is not None else row["g1_reversal_probability"]
    return ResearchG2(
        event_id=int(row["event_id"]),
        symbol=str(row["symbol"]),
        market_story=str(market_story),
        bullish_factors=bullish,
        bearish_factors=json.loads(row["bearish_factors_json"] or "[]") if "bearish_factors_json" in cols else [],
        reversal_probability=float(rev_p or 0),
        continuation_probability=float(row["continuation_probability"] or 0.5) if "continuation_probability" in cols else 0.5,
        risks=risks,
        invalidates=invalidates,
        summary_ru=str(summary),
        telegram_block=str(row["telegram_block"] or ""),
        provider=str(row["provider"]),
        confidence=float(row["confidence"] or 0) if "confidence" in cols else 0.0,
        market_score=float(row["market_score"] or 0) if "market_score" in cols else 0.0,
    )
