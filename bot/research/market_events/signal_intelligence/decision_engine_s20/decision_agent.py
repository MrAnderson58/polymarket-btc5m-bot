"""S2.0/S3.1 Decision Agent — merges Market + News + Pattern; Claude does not recompute market."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM = """You are the Decision Agent for a crypto research desk.
You receive ONLY agent JSON blobs: Market, News, and optionally Pattern.
Do NOT invent prices, recompute indicators, or search for historical cases yourself.
Use Pattern Agent fields (historical_wr, sample_size, confidence) when present.
Merge conclusions, assess agreement, explain risks, return a final trade decision.
Output ONLY valid JSON with keys:
decision (LONG|SHORT|FLAT), probability (0-100 int), confidence (0-10 float),
summary (string), risks (array of strings).
"""


def _fallback_merge(
    market: dict[str, Any],
    news: dict[str, Any],
    pattern: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic merge when Claude is unavailable. No market math."""
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
        if n_imp == "high" and n_conf >= 0.7:
            decision = "FLAT"
        else:
            decision = m_dir

    pat = pattern or {}
    pat_found = bool(pat.get("pattern_found"))
    pat_wr = float(pat.get("historical_wr") or 0.0)
    pat_n = int(pat.get("sample_size") or 0)
    pat_conf = float(pat.get("confidence") or 0.0)

    base_prob = int(round(50 + (m_conf - 0.5) * 60 + (0.15 if agree else -0.2) * 100 * n_conf))
    if pat_found and pat_n >= 5:
        # nudge probability toward historical WR
        base_prob = int(round(base_prob * 0.7 + pat_wr * 100 * 0.3))
    if decision == "FLAT":
        base_prob = min(base_prob, 55)
    probability = max(35, min(92, base_prob))
    confidence = round(
        max(
            3.0,
            min(
                9.5,
                (m_conf * 5.0 + n_conf * 3.0 + (pat_conf if pat_found else 0) * 2.0)
                * (1.05 if agree else 0.85),
            ),
        ),
        1,
    )

    risks: list[str] = []
    if not agree and news_dir != "FLAT":
        risks.append(f"Market ({m_dir}) and news ({n_sent}) disagree")
    if n_imp == "high":
        risks.append("High-importance news may invalidate technical setup quickly")
    if float(market.get("rr") or 0) < 1.5:
        risks.append("Market agent RR is modest — asymmetric payoff limited")
    if pat_found and pat_wr < 0.45:
        risks.append(f"Pattern historical WR low ({pat_wr:.0%}, n={pat_n})")
    if not pat_found:
        risks.append("Pattern Agent: insufficient similar history")
    risks.append("Research-only decision; not wired to production execution")

    m_reasons = market.get("reasons") or []
    n_reasons = news.get("reasons") or []
    summary = (
        f"Market {m_dir} (conf {m_conf:.2f}) with news {n_sent} "
        f"(conf {n_conf:.2f}, {n_imp}). "
        + ("Agents aligned. " if agree else "Partial conflict resolved toward market. ")
        + (
            f"Pattern {pat.get('pattern_key')} WR {pat_wr:.0%} n={pat_n}. "
            if pat_found
            else "Pattern not found. "
        )
        + (f"Key market: {m_reasons[0]}" if m_reasons else "")
        + (f" Key news: {n_reasons[0]}" if n_reasons else "")
    ).strip()

    return {
        "decision": decision,
        "probability": probability,
        "confidence": confidence,
        "summary": summary[:500],
        "risks": risks[:8],
        "meta": {"claude": False, "mode": "fallback_merge", "pattern_used": pat_found},
    }


def run_decision_agent_s20(
    market: dict[str, Any],
    news: dict[str, Any],
    *,
    pattern: dict[str, Any] | None = None,
    allow_claude: bool = True,
    force_fallback: bool = False,
) -> dict[str, Any]:
    """Claude merge of Market + News + Pattern. Fallback merge if Claude unavailable."""
    if force_fallback or not allow_claude:
        return _fallback_merge(market, news, pattern)

    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            call_claude_json_g2,
            is_claude_configured,
        )
    except Exception:
        return _fallback_merge(market, news, pattern)

    if not is_claude_configured():
        return _fallback_merge(market, news, pattern)

    m_payload = {k: v for k, v in market.items() if k != "meta"}
    n_payload = {k: v for k, v in news.items() if k != "meta"}
    p_payload = None
    if pattern:
        p_payload = {k: v for k, v in pattern.items() if k != "meta"}

    prompt = (
        "Market Agent JSON:\n"
        + json.dumps(m_payload, ensure_ascii=False)
        + "\n\nNews Agent JSON:\n"
        + json.dumps(n_payload, ensure_ascii=False)
    )
    if p_payload is not None:
        prompt += (
            "\n\nPattern Agent JSON (do NOT recompute history; use as given):\n"
            + json.dumps(p_payload, ensure_ascii=False)
        )
    prompt += "\n\nReturn the final decision JSON."

    try:
        parsed, resp = call_claude_json_g2(
            system=_SYSTEM,
            prompt=prompt,
            label="s31_decision",
        )
        decision = str(parsed.get("decision") or "FLAT").upper()
        if decision not in ("LONG", "SHORT", "FLAT"):
            decision = "FLAT"
        probability = max(0, min(100, int(parsed.get("probability") or 50)))
        confidence = max(0.0, min(10.0, float(parsed.get("confidence") or 5.0)))
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
                "pattern_used": bool(pattern and pattern.get("pattern_found")),
                "usage": {
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                    "cost_usd": resp.usage.cost_usd,
                },
            },
        }
    except Exception as exc:
        logger.warning("Decision Agent Claude failed: %s — fallback merge", exc)
        out = _fallback_merge(market, news, pattern)
        out["meta"] = {**(out.get("meta") or {}), "claude_error": str(exc)[:200]}
        return out
