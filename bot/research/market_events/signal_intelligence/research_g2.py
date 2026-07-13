"""Phase G.2 — Claude Research Agent (explanation only, no trading decisions)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import (
    G2_ENABLED,
    G2_MIN_CONFIDENCE,
    G2_MIN_G1_REVERSAL_PROB,
    G2_MIN_MARKET_SCORE,
    PROMPT_VERSION_G2,
)
from bot.research.market_events.signal_intelligence.liquidity_trend_g1 import load_liquidity_trend_g1
from bot.research.market_events.signal_intelligence.telegram_g2 import format_research_telegram_g2
from bot.research.market_events.signal_intelligence.visual_analysis_g2 import run_visual_analysis_g2

logger = logging.getLogger(__name__)

_TABLE = "market_events_ai_research_g2"

_G2_JSON_SCHEMA = {
    "market_story": "string",
    "bullish_factors": ["strings"],
    "bearish_factors": ["strings"],
    "reversal_probability": "0-1",
    "continuation_probability": "0-1",
    "risks": ["strings"],
    "invalidates": ["strings"],
    "summary_ru": "one line Russian",
}

_SYSTEM_PROMPT = """You are a market research analyst (Claude Sonnet).
Explain market structure only. No trades. No engine score changes.
Answer in Russian inside JSON fields.
Output ONLY valid JSON with keys:
market_story, bullish_factors, bearish_factors, reversal_probability,
continuation_probability, risks, invalidates, summary_ru."""


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

    conf = float(f7.final_confidence)
    mscore = float(f7.market_score)
    rev = float(g1.reversal_probability)

    if conf < G2_MIN_CONFIDENCE:
        return False, f"confidence {conf:.1f}"
    if mscore < G2_MIN_MARKET_SCORE:
        return False, f"market score {mscore:.0f}"
    if rev < G2_MIN_G1_REVERSAL_PROB:
        return False, f"reversal {rev:.2f}"
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
    me = conn.execute(
        "SELECT return_pct, direction FROM market_events WHERE id = ?",
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
        "market_event": dict(me) if me else None,
        "f2": dict(f2) if f2 else None,
        "trend_v2": dict(trend) if trend else None,
    }


def build_g2_compact_summary(ctx: dict[str, Any]) -> dict[str, Any]:
    """Compact Claude input — omits duplicate F5/F7 blobs and F0 bundle."""
    g1 = ctx.get("g1") or {}
    me = ctx.get("market_event") or {}
    f5 = ctx.get("f5") or {}
    f7 = ctx.get("f7") or {}
    f2 = ctx.get("f2") or {}
    trend = ctx.get("trend_v2") or {}
    liq = g1.get("liquidity_accum") or {}
    cap = g1.get("capitulation") or {}
    patterns = g1.get("consecutive_patterns") or []
    best_streak = max((int(p.get("max_streak") or 0) for p in patterns), default=0)
    slow = g1.get("slow_trend") or {}
    hist = float(f2.get("historical_reversal_rate") or 0) if f2 else 0.0

    return {
        "sym": ctx.get("symbol"),
        "ret": round(float(me.get("return_pct") or 0), 2),
        "dir": me.get("direction"),
        "f5_conf": f5.get("dynamic_confidence"),
        "f5_rev": f5.get("reversal_probability"),
        "f5_cause": (f5.get("signal_cause") or "")[:80] or None,
        "f7_score": f7.get("market_score"),
        "f7_conf": f7.get("final_confidence"),
        "f7_liq": f7.get("liquidation_regime"),
        "f7_dom": f7.get("dominance_regime"),
        "g1_type": g1.get("signal_type"),
        "g1_rev": g1.get("reversal_probability"),
        "g1_cont": g1.get("continuation_probability"),
        "fund_neg": bool(liq.get("funding_negative")),
        "oi_up": bool(liq.get("oi_rising")),
        "cap_vol": cap.get("volume_multiple"),
        "streak": best_streak or None,
        "slow": (slow.get("description") or "")[:60] or None,
        "trend": trend.get("stage"),
        "corr": f2.get("correlation_verdict") if f2 else None,
        "hist_rev": round(hist, 2) if hist else None,
    }


def build_g2_prompt(ctx: dict[str, Any]) -> str:
    compact = build_g2_compact_summary(ctx)
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"), default=str)


def estimate_g2_prompt_tokens(ctx: dict[str, Any]) -> int:
    """Rough token estimate for regression budget checks."""
    prompt = build_g2_prompt(ctx)
    return max(1, len(prompt) // 4)


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


def _call_claude_research(
    conn: Any,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], str, str, int, int, float, float, str, str | None, str]:
    from bot.research.market_events.signal_intelligence.claude_client_g2 import (
        ClaudeClientError,
        call_claude_json_g2,
        default_model,
        is_claude_configured,
    )
    from bot.research.market_events.signal_intelligence.claude_ops_g2 import (
        AI_STATUS_CACHED,
        AI_STATUS_DETERMINISTIC,
        AI_STATUS_OK,
        AI_STATUS_RATE_LIMITED,
        AI_STATUS_SKIPPED,
        record_claude_failure,
        record_claude_success,
        try_consume_claude_quota,
    )
    from bot.research.market_events.signal_intelligence.claude_cache_g2 import (
        compute_g2_context_hash,
        load_prompt_cache,
        save_prompt_cache,
    )

    in_tok = out_tok = 0
    cost = 0.0
    latency = 0.0
    compact = build_g2_compact_summary(ctx)
    context_hash = compute_g2_context_hash(compact)

    if not is_claude_configured():
        fallback = analyze_research_g2_deterministic(ctx)
        return fallback, "deterministic", "g2_rules_v1", in_tok, out_tok, cost, latency, AI_STATUS_DETERMINISTIC, None, context_hash

    cached = load_prompt_cache(conn, context_hash)
    if cached:
        result = _parse_g2_response(cached["parsed"])
        if result:
            return (
                result, cached["provider"], cached["model"],
                cached["input_tokens"], cached["output_tokens"], cached["cost_usd"], 0.0,
                AI_STATUS_CACHED, None, context_hash,
            )

    allowed, limit_reason = try_consume_claude_quota(conn)
    if not allowed:
        fallback = analyze_research_g2_deterministic(ctx)
        return (
            fallback, "deterministic", "g2_rules_v1",
            in_tok, out_tok, cost, latency,
            AI_STATUS_RATE_LIMITED, limit_reason, context_hash,
        )

    model = default_model()
    try:
        parsed, resp = call_claude_json_g2(
            system=_SYSTEM_PROMPT,
            prompt=build_g2_prompt(ctx),
            label="g2_research",
        )
        result = _parse_g2_response(parsed)
        if not result:
            raise ClaudeClientError("invalid_json", "response missing required schema fields")
        record_claude_success(conn, usage=resp.usage, model=resp.model)
        save_prompt_cache(
            conn,
            context_hash=context_hash,
            response=result,
            provider="anthropic",
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cost_usd=resp.usage.cost_usd,
        )
        return (
            result, "anthropic", resp.model,
            resp.usage.input_tokens, resp.usage.output_tokens,
            resp.usage.cost_usd, resp.usage.latency_ms,
            AI_STATUS_OK, None, context_hash,
        )
    except ClaudeClientError as exc:
        record_claude_failure(conn, error=f"{exc.kind}: {exc.message}", model=model)
        logger.warning("g2 claude research AI_SKIPPED: %s", exc)
        fallback = analyze_research_g2_deterministic(ctx)
        return (
            fallback, "deterministic", "g2_rules_v1",
            in_tok, out_tok, cost, latency,
            AI_STATUS_SKIPPED, str(exc), context_hash,
        )
    except Exception as exc:
        record_claude_failure(conn, error=str(exc), model=model)
        logger.warning("g2 claude research AI_SKIPPED: %s", exc)
        fallback = analyze_research_g2_deterministic(ctx)
        return (
            fallback, "deterministic", "g2_rules_v1",
            in_tok, out_tok, cost, latency,
            AI_STATUS_SKIPPED, str(exc), context_hash,
        )


def run_claude_research_g2(conn: Any, event_id: int, *, force: bool = False) -> ResearchG2 | None:
    """Research-only second opinion. Never changes confidence or opens trades."""
    if not force:
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

    result, provider, model, in_tok, out_tok, cost, latency, ai_status, skip_error, context_hash = _call_claude_research(conn, ctx)

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
          ai_status, skip_error, context_hash,
          provider, model, prompt_version, response_json, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ai_status, skip_error, context_hash,
            provider, model, PROMPT_VERSION_G2,
            json.dumps({**result, "visual": visual, "ai_status": ai_status, "skip_error": skip_error}, ensure_ascii=False),
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


def _g2_skip_display(reason: str) -> str:
    if reason.startswith("market score"):
        return f"Market Score below threshold ({reason.split()[-1]})"
    if reason.startswith("confidence"):
        return f"Confidence below threshold ({reason.split()[-1]})"
    if reason.startswith("reversal"):
        return f"Reversal probability below threshold ({reason.split()[-1]})"
    return reason


def run_research_g2(conn: Any, event_id: int, *, force: bool = False) -> ResearchG2 | None:
    """Production pipeline entry — trace + eligibility + Claude research."""
    from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
        record_g2_completed,
        record_g2_skipped,
        record_g2_started,
    )

    if not G2_ENABLED and not force:
        record_g2_skipped(conn, event_id=event_id, reason="disabled")
        return None

    record_g2_started(conn, event_id=event_id)

    if not force:
        ok, reason = check_g2_eligibility(conn, event_id)
        if not ok:
            record_g2_skipped(conn, event_id=event_id, reason=_g2_skip_display(reason))
            return None

    existing = conn.execute(
        f"SELECT provider, latency_ms, input_tokens, output_tokens, cost_usd, ai_status "
        f"FROM {_TABLE} WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if existing:
        record_g2_completed(
            conn,
            event_id=event_id,
            provider=str(existing["provider"]),
            latency_ms=float(existing["latency_ms"] or 0),
            input_tokens=int(existing["input_tokens"] or 0),
            output_tokens=int(existing["output_tokens"] or 0),
            cost_usd=float(existing["cost_usd"] or 0),
            ai_status=str(existing["ai_status"] or "CACHED"),
        )
        return load_research_g2(conn, event_id)

    result = run_claude_research_g2(conn, event_id, force=force)
    if not result:
        record_g2_skipped(conn, event_id=event_id, reason="no G1 context")
        return None

    row = conn.execute(
        f"SELECT provider, latency_ms, input_tokens, output_tokens, cost_usd, ai_status "
        f"FROM {_TABLE} WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if row:
        record_g2_completed(
            conn,
            event_id=event_id,
            provider=str(row["provider"]),
            latency_ms=float(row["latency_ms"] or 0),
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            cost_usd=float(row["cost_usd"] or 0),
            ai_status=str(row["ai_status"] or "OK"),
        )
    return result


def resolve_latest_g2_event_id(conn: Any) -> int | None:
    row = conn.execute(
        f"SELECT event_id FROM {_TABLE} ORDER BY created_at DESC LIMIT 1",
    ).fetchone()
    if row:
        return int(row["event_id"])
    trace = conn.execute(
        """
        SELECT event_id FROM market_events_signal_trace_f51
        WHERE stage IN ('G2 STARTED', 'G2 COMPLETED', 'G2 SKIPPED')
        ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()
    return int(trace["event_id"]) if trace else None


def _f7_trace_status(conn: Any, event_id: int) -> tuple[str, list[str]]:
    from bot.research.market_events.signal_intelligence.market_intel_f7 import (
        diagnose_f7_skip,
        load_market_intelligence_f7,
    )
    from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
        STAGE_F7_COMPLETED,
        STAGE_F7_SKIPPED,
        fetch_trace_rows,
    )

    rows = fetch_trace_rows(conn, event_id=event_id)
    completed = next((r for r in rows if r["stage"] == STAGE_F7_COMPLETED), None)
    skipped = next((r for r in rows if r["stage"] == STAGE_F7_SKIPPED), None)
    if completed:
        parts = ["completed"]
        if completed.get("reason"):
            parts.extend(str(completed["reason"]).split("\n"))
        return "F7", parts
    if skipped:
        return "F7 skipped", [skipped.get("reason") or "unknown"]
    f7 = load_market_intelligence_f7(conn, event_id)
    if f7:
        return "F7", ["completed", f"score {float(f7.market_score):.0f}"]
    reason = diagnose_f7_skip(conn, event_id) or "market score unavailable"
    return "F7 skipped", [reason]


def format_g2_trace(conn: Any, event_id: int | None = None) -> str:
    """Compact G2 pipeline trace for CLI."""
    if event_id is None:
        event_id = resolve_latest_g2_event_id(conn)
    if event_id is None:
        return "No G2 events found."

    from bot.research.market_events.signal_intelligence.professional_signal_f5 import load_professional_signal_f5
    from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
        STAGE_G2_COMPLETED,
        STAGE_G2_SKIPPED,
        fetch_trace_rows,
    )

    lines = [f"Event {event_id}", ""]

    f5 = load_professional_signal_f5(conn, event_id)
    lines.append("F5")
    if f5:
        lines.extend(["passed", f"{float(f5.dynamic_confidence):.1f}"])
    else:
        lines.append("skipped")
        lines.append("F5 signal unavailable")
    lines.append("")

    f7_label, f7_parts = _f7_trace_status(conn, event_id)
    lines.append(f7_label)
    lines.extend(f7_parts)
    lines.append("")

    trace_rows = fetch_trace_rows(conn, event_id=event_id)
    g2_completed = next((r for r in trace_rows if r["stage"] == STAGE_G2_COMPLETED), None)
    g2_skipped = next((r for r in trace_rows if r["stage"] == STAGE_G2_SKIPPED), None)
    g2_row = conn.execute(
        f"SELECT provider, latency_ms, input_tokens, output_tokens, cost_usd, ai_status "
        f"FROM {_TABLE} WHERE event_id = ?",
        (event_id,),
    ).fetchone()

    if g2_completed or (g2_row and g2_row["provider"]):
        lines.append("G2")
        status = "SUCCESS"
        if g2_row and g2_row["ai_status"] == "CACHED":
            status = "CACHED"
        elif g2_row and g2_row["ai_status"] not in ("OK", "CACHED"):
            status = str(g2_row["ai_status"])
        if g2_completed and g2_completed.get("reason"):
            for part in str(g2_completed["reason"]).split("\n"):
                lines.append(part)
        elif g2_row:
            total = int(g2_row["input_tokens"] or 0) + int(g2_row["output_tokens"] or 0)
            lines.extend([
                "called Claude" if g2_row["provider"] == "anthropic" else f"provider {g2_row['provider']}",
                f"latency {float(g2_row['latency_ms'] or 0):.0f} ms",
                f"tokens {total}",
                f"cost ${float(g2_row['cost_usd'] or 0):.4f}",
            ])
        lines.append(f"status {status}")
    elif g2_skipped:
        lines.append("G2 skipped")
        lines.append(g2_skipped.get("reason") or "unknown")
    else:
        lines.append("G2")
        lines.append("not run")

    return "\n".join(lines)
