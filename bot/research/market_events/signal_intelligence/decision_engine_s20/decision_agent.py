"""S2.0 Decision Agent — Claude only; merges Market + News outputs, no own market math."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM = """You are the Decision Agent for a crypto research desk.
You receive ONLY two JSON blobs: Market Agent and News Agent.
Do NOT invent prices or recompute indicators. Merge their conclusions.
Assess agreement, explain risks, and return a final trade decision.
Output ONLY valid JSON with keys:
decision (LONG|SHORT|FLAT), probability (0-100 int), confidence (0-10 float),
summary (string), risks (array of strings).
"""


def _fallback_merge(market: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    """Deterministic merge when Claude is unavailable (tests / offline). No market math."""
    m_dir = str(market.get("direction") or "FLAT").upper()
    m_conf = float(market.get("confidence") or 0.0)
    n_sent = str(news.get("sentiment") or "neutral").lower()
    n_conf = float(news.get("confidence") or 0.0)
    n_imp = str(news.get("importance") or "low").lower()

    news_dir = "LONG" if n_sent == "bullish" else "SHORT" if n_sent == "bearish" else "FLAT"
    agree = (m_dir == news_dir) or (news_dir == "FLAT")

    if m_dir in ("LONG", "SHORT"):
        decision = m_dir
    else:
        decision = news_dir if news_dir != "FLAT" else "FLAT"

    if news_dir != "FLAT" and m_dir != "FLAT" and news_dir != m_dir:
        # Conflict → lower conviction / lean FLAT if news high importance
        if n_imp == "high" and n_conf >= 0.7:
            decision = "FLAT"
        else:
            decision = m_dir

    base_prob = int(round(50 + (m_conf - 0.5) * 60 + (0.15 if agree else -0.2) * 100 * n_conf))
    if decision == "FLAT":
        base_prob = min(base_prob, 55)
    probability = max(35, min(92, base_prob))
    confidence = round(max(3.0, min(9.5, (m_conf * 6.0 + n_conf * 4.0) * (1.05 if agree else 0.85))), 1)

    risks: list[str] = []
    if not agree and news_dir != "FLAT":
        risks.append(f"Market ({m_dir}) and news ({n_sent}) disagree")
    if n_imp == "high":
        risks.append("High-importance news may invalidate technical setup quickly")
    if float(market.get("rr") or 0) < 1.5:
        risks.append("Market agent RR is modest — asymmetric payoff limited")
    risks.append("Research-only decision; not wired to production execution")

    m_reasons = market.get("reasons") or []
    n_reasons = news.get("reasons") or []
    summary = (
        f"Market {m_dir} (conf {m_conf:.2f}) with news {n_sent} "
        f"(conf {n_conf:.2f}, {n_imp}). "
        + ("Agents aligned. " if agree else "Partial conflict resolved toward market. ")
        + (f"Key market: {m_reasons[0]}" if m_reasons else "")
        + (f" Key news: {n_reasons[0]}" if n_reasons else "")
    ).strip()

    return {
        "decision": decision,
        "probability": probability,
        "confidence": confidence,
        "summary": summary[:500],
        "risks": risks[:6],
        "meta": {"claude": False, "mode": "fallback_merge"},
    }


def run_decision_agent_s20(
    market: dict[str, Any],
    news: dict[str, Any],
    *,
    allow_claude: bool = True,
    force_fallback: bool = False,
) -> dict[str, Any]:
    """Claude merge of Agent1+Agent2 only. Fallback merge if Claude unavailable."""
    if force_fallback or not allow_claude:
        return _fallback_merge(market, news)

    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            ClaudeClientError,
            call_claude_json_g2,
            is_claude_configured,
        )
    except Exception:
        return _fallback_merge(market, news)

    if not is_claude_configured():
        return _fallback_merge(market, news)

    # Strip large meta blobs for prompt clarity but keep reasons/levels
    m_payload = {k: v for k, v in market.items() if k != "meta"}
    n_payload = {k: v for k, v in news.items() if k != "meta"}
    prompt = (
        "Market Agent JSON:\n"
        + json.dumps(m_payload, ensure_ascii=False)
        + "\n\nNews Agent JSON:\n"
        + json.dumps(n_payload, ensure_ascii=False)
        + "\n\nReturn the final decision JSON."
    )
    try:
        parsed, resp = call_claude_json_g2(
            system=_SYSTEM,
            prompt=prompt,
            label="s20_decision",
        )
        decision = str(parsed.get("decision") or "FLAT").upper()
        if decision not in ("LONG", "SHORT", "FLAT"):
            decision = "FLAT"
        probability = int(parsed.get("probability") or 50)
        probability = max(0, min(100, probability))
        confidence = float(parsed.get("confidence") or 5.0)
        confidence = max(0.0, min(10.0, confidence))
        summary = str(parsed.get("summary") or "").strip() or "Claude decision merge"
        risks = parsed.get("risks") or []
        if not isinstance(risks, list):
            risks = [str(risks)]
        return {
            "decision": decision,
            "probability": probability,
            "confidence": round(confidence, 1),
            "summary": summary[:800],
            "risks": [str(r) for r in risks][:8],
            "meta": {
                "claude": True,
                "model": resp.model,
                "usage": {
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                    "cost_usd": resp.usage.cost_usd,
                },
            },
        }
    except Exception as exc:
        logger.warning("S2.0 Decision Agent Claude failed: %s — fallback merge", exc)
        out = _fallback_merge(market, news)
        out["meta"] = {**(out.get("meta") or {}), "claude_error": str(exc)[:200]}
        return out
