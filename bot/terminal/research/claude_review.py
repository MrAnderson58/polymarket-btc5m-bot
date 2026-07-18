"""Claude review of a formed DecisionCard — not blank-slate analysis."""

from __future__ import annotations

import re
from typing import Any

from bot.terminal.research.models import ResearchContext, ResearchReview, ReviewVerdict

_SYSTEM = """You are a trading research reviewer for an AI Trading Terminal.
You do NOT invent a new thesis from scratch.
You REVIEW an already-formed DecisionCard using Scanner, Portfolio, News, and Learning context.

Rules:
- Be concise and decisive.
- Confirm, caution, or reject the existing idea.
- Prefer adjustments to size/levels over rewriting the whole idea.
- Never claim live order execution.
- Respond in JSON only with keys:
  verdict (confirm|caution|reject),
  summary (string),
  strengths (string array),
  risks (string array),
  adjustments (string array).
"""


def template_review(ctx: ResearchContext) -> ResearchReview:
    """Deterministic fallback when Claude is unavailable."""
    low = ctx.decision_text.lower()
    verdict: ReviewVerdict = "confirm"
    if "no decisioncard" in low or "score=0" in low.replace(" ", ""):
        verdict = "reject"
    elif "⚠" in ctx.decision_text or "crypto=" in ctx.portfolio_text:
        # Heavy crypto book → caution
        if "crypto=" in ctx.portfolio_text:
            try:
                # crypto=72%
                m = re.search(r"crypto=([0-9.]+)", ctx.portfolio_text)
                if m and float(m.group(1)) >= 60:
                    verdict = "caution"
            except ValueError:
                verdict = "caution"
    strengths = []
    for line in ctx.decision_text.splitlines():
        if line.strip().startswith("✓"):
            strengths.append(line.strip())
        if len(strengths) >= 3:
            break
    risks = []
    for line in ctx.decision_text.splitlines():
        if line.strip().startswith("⚠"):
            risks.append(line.strip())
        if len(risks) >= 3:
            break
    if not risks:
        risks = ["Review macro window", "Check portfolio concentration"]
    adjustments = []
    if verdict == "caution":
        adjustments = ["Reduce size", "Prefer hedges (Gold) if crypto-heavy"]
    elif verdict == "reject":
        adjustments = ["Do not enter — wait for DecisionCard"]
    else:
        adjustments = ["Keep plan; trail risk to stop"]
    summary = (
        f"Template review ({verdict}) for {ctx.symbol}: "
        "idea already formed — validating against book/news/learning."
    )
    return ResearchReview(
        symbol=ctx.symbol,
        verdict=verdict,
        summary=summary,
        strengths=tuple(strengths) or ("Decision structure present",),
        risks=tuple(risks),
        adjustments=tuple(adjustments),
        provider="template",
    )


def _parse_verdict(raw: str) -> ReviewVerdict:
    v = (raw or "").strip().lower()
    if v in {"confirm", "caution", "reject"}:
        return v  # type: ignore[return-value]
    return "unknown"


def _from_payload(symbol: str, data: dict[str, Any], *, model: str, raw_text: str) -> ResearchReview:
    return ResearchReview(
        symbol=symbol,
        verdict=_parse_verdict(str(data.get("verdict") or "unknown")),
        summary=str(data.get("summary") or "").strip() or "Claude review returned empty summary",
        strengths=tuple(str(x) for x in (data.get("strengths") or [])[:6]),
        risks=tuple(str(x) for x in (data.get("risks") or [])[:6]),
        adjustments=tuple(str(x) for x in (data.get("adjustments") or [])[:6]),
        provider="claude",
        model=model,
        raw_text=raw_text,
    )


def claude_review(
    ctx: ResearchContext,
    *,
    use_telegram_channel: bool = True,
) -> ResearchReview:
    """Call existing G.2 Claude client to review packed context."""
    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            ClaudeClientError,
            call_claude_json_g2,
            is_claude_configured,
        )
        from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
            telegram_claude_session,
        )
    except Exception:
        return template_review(ctx)

    if not is_claude_configured():
        return template_review(ctx)

    user_prompt = (
        "Review the following already-formed trading decision.\n"
        "Do not rebuild the idea from scratch.\n\n"
        + ctx.as_prompt_block()
    )

    try:
        def _call() -> tuple[dict[str, Any], Any]:
            return call_claude_json_g2(
                system=_SYSTEM,
                prompt=user_prompt,
                label="terminal_research_v703",
            )

        if use_telegram_channel:
            with telegram_claude_session():
                data, resp = _call()
        else:
            data, resp = _call()
        if not isinstance(data, dict):
            return template_review(ctx)
        return _from_payload(ctx.symbol, data, model=resp.model, raw_text=resp.text)
    except ClaudeClientError:
        return template_review(ctx)
    except Exception:
        return template_review(ctx)


def format_research_review(review: ResearchReview) -> str:
    parts = [
        f"🔍 Research Review — {review.symbol}",
        f"Verdict: {review.verdict.upper()}",
        f"Provider: {review.provider}" + (f" ({review.model})" if review.model else ""),
        "",
        review.summary,
        "",
        "Strengths",
        *(f"• {s}" for s in (review.strengths or ("—",))),
        "",
        "Risks",
        *(f"• {r}" for r in (review.risks or ("—",))),
        "",
        "Adjustments",
        *(f"• {a}" for a in (review.adjustments or ("—",))),
    ]
    return "\n".join(parts)


__all__ = [
    "claude_review",
    "format_research_review",
    "template_review",
]
