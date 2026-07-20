"""S46 multi-provider LLM client (openai | anthropic | openrouter | ollama | template)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

from bot.research.ai_analyst.config import LLMSettings, load_llm_settings

logger = logging.getLogger(__name__)

_RETRYABLE = frozenset({408, 429, 500, 502, 503, 529})


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: float
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str) -> LLMResponse:
        ...


class TemplateLLMClient:
    """Deterministic offline analyst — used when no API key / provider=template."""

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def complete(self, *, system: str, user: str) -> LLMResponse:
        t0 = time.perf_counter()
        # Extract JSON context blob from user message if present.
        ctx_json = "{}"
        if "--- MARKET CONTEXT JSON" in user:
            try:
                chunk = user.split("--- MARKET CONTEXT JSON", 1)[1]
                chunk = chunk.split("--- END CONTEXT ---", 1)[0]
                # strip header line
                lines = chunk.strip().splitlines()
                if lines and lines[0].startswith("("):
                    lines = lines[1:]
                ctx_json = "\n".join(lines).strip()
            except Exception:
                ctx_json = "{}"
        try:
            ctx = json.loads(ctx_json) if ctx_json else {}
        except Exception:
            ctx = {}
        text = _template_from_context(ctx, user=user)
        return LLMResponse(
            text=text,
            provider="template",
            model="template",
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )


def _task_header(user: str) -> str:
    """Prompt instructions only (exclude appended market context JSON)."""
    if "--- MARKET CONTEXT JSON" in user:
        return user.split("--- MARKET CONTEXT JSON", 1)[0].lower()
    return (user or "").lower()


def _template_from_context(ctx: dict[str, Any], *, user: str) -> str:
    """Rule-based report when LLM unavailable (still produces all artifacts)."""
    btc = ctx.get("btc") or {}
    spx = ctx.get("sp500") or {}
    macro = ctx.get("macro") or {}
    intel = ctx.get("intelligence") or {}
    events = intel.get("top_events") or []
    poly = ctx.get("polymarket") or {}
    fg = ctx.get("fear_greed") or {}
    fund = ctx.get("funding") or {}
    oi = ctx.get("open_interest") or {}

    lead = events[0].get("title") if events else "Limited event coverage in lookback"
    bias = "neutral"
    sent = 0.0
    if events:
        sent = float(events[0].get("sentiment") or 0)
        if sent > 0.1:
            bias = "bullish"
        elif sent < -0.1:
            bias = "bearish"

    btc_px = btc.get("price")
    spx_px = spx.get("price")
    risk = "MEDIUM"
    if (fg.get("current") is not None and float(fg["current"]) <= 25) or abs(sent) > 0.4:
        risk = "HIGH"
    elif abs(sent) < 0.1:
        risk = "LOW"

    task = _task_header(user)
    if "exact keys" in task or "json object" in task or "market_bias" in task:
        return json.dumps({
            "market_bias": bias,
            "risk_level": risk,
            "main_theme": str(lead)[:180],
            "btc_summary": (
                f"BTC price={btc_px}; 24h={btc.get('change_24h_pct')}; "
                f"dominance={btc.get('dominance')}"
            ),
            "sp500_summary": f"SPX price={spx_px}; change={spx.get('change_pct')}",
            "macro_summary": (
                f"DXY={macro.get('dxy')}; US10Y={macro.get('us10y')}; "
                f"Fed={macro.get('fed')}"
            ),
            "top_events": [str(e.get("title") or "")[:120] for e in events[:5]],
            "top_risks": [
                "Data gaps in snapshot fields" if btc_px is None else "Event-driven volatility",
                f"Funding={fund.get('current')}; OI={oi.get('current')}",
            ],
            "next_24h": "Monitor event freshness, funding extremes, and macro prints in context.",
        }, ensure_ascii=False, indent=2)

    if "280 characters" in task or "x/twitter" in task:
        parts = [
            f"Market bias {bias}.",
            f"Lead: {str(lead)[:90]}.",
            f"Risk {risk}. Context only.",
        ]
        return " ".join(parts)[:280]

    if "telegram" in task or "русский" in task:
        lines = [
            f"Обзор рынка ({bias}, риск {risk}).",
            f"Главное: {str(lead)[:160]}",
            f"BTC: {btc_px}; SPX: {spx_px}; F&G: {fg.get('current')}",
            "Только контекст, не рекомендация.",
        ]
        return "\n".join(lines)[:800]

    if "morning brief" in task:
        return "\n".join([
            "## Morning Brief",
            f"**Bias:** {bias} | **Risk:** {risk}",
            f"**Lead:** {lead}",
            f"**BTC:** price={btc_px}, 24h={btc.get('change_24h_pct')}",
            f"**Macro:** DXY={macro.get('dxy')}, US10Y={macro.get('us10y')}",
            "**Note:** Template mode — LLM not configured. No trading advice.",
        ])

    if "evening brief" in task:
        return "\n".join([
            "## Evening Brief",
            f"**Day bias:** {bias} | **Risk:** {risk}",
            f"**What moved:** {lead}",
            f"**BTC 24h:** {btc.get('change_24h_pct')} | **SPX change:** {spx.get('change_pct')}",
            "**Overnight watch:** funding, OI delta, fresh intel events.",
            "**Note:** Template mode. No trading advice.",
        ])

    if "btc brief" in task or "bitcoin-only" in task:
        return "\n".join([
            "## BTC Brief",
            f"1. Drivers: {lead}",
            f"2. Changes: 1h={btc.get('change_1h_pct')}, 24h={btc.get('change_24h_pct')}, "
            f"7d={btc.get('change_7d_pct')}",
            f"3. Risks: funding={fund.get('current')}, fear_greed={fg.get('current')}",
            f"4. Supports: dominance={btc.get('dominance')}, volume={btc.get('volume')}",
            "5. Watch: ETF/intel events and funding extremes in context.",
            "No trading advice.",
        ])

    if "macro brief" in task:
        return "\n".join([
            "## Macro Brief",
            f"Economy snapshot: DXY={macro.get('dxy')}, US10Y={macro.get('us10y')}, "
            f"US02Y={macro.get('us02y')}, Gold={macro.get('gold')}, Oil={macro.get('oil')}, "
            f"Fed={macro.get('fed')}, CPI={macro.get('cpi')}.",
            "Risk assets: sensitivity rises when DXY/Fed narrative dominates context.",
            f"BTC: linked via risk tone; lead event={lead}",
            f"S&P500: price={spx_px}; VIX={ctx.get('vix', {}).get('current')}",
            "No trading advice.",
        ])

    if "s&p500 brief" in task or "sp500 brief" in task:
        return "\n".join([
            "## S&P500 Brief",
            f"State: price={spx_px}, change={spx.get('change_pct')}, "
            f"ATH distance={spx.get('distance_to_ath_pct')}",
            f"Sentiment: VIX={ctx.get('vix', {}).get('current')}, F&G={fg.get('current')}",
            "Sector leaders: unavailable in context.",
            f"BTC link: shared risk tone; BTC={btc_px}",
            "No trading advice.",
        ])

    # Full report default
    ev_lines = []
    for e in events[:5]:
        ev_lines.append(
            f"- {e.get('title')} | impact={e.get('market_impact')} | "
            f"{e.get('polarity')} | {str(e.get('why_it_matters') or '')[:160]}"
        )
    if not ev_lines:
        ev_lines = ["- Limited intel event coverage"]

    return "\n".join([
        "## Executive Summary",
        f"Bias {bias}, risk {risk}. Lead development: {lead}. "
        "Template analyst used (no LLM). Context-only; not trading advice.",
        "",
        "## Global Market Overview",
        f"BTC={btc_px}, SPX={spx_px}, VIX={ctx.get('vix', {}).get('current')}, "
        f"F&G={fg.get('current')}.",
        "",
        "## Bitcoin",
        f"Price={btc_px}; 1h={btc.get('change_1h_pct')}; 24h={btc.get('change_24h_pct')}; "
        f"7d={btc.get('change_7d_pct')}; vol={btc.get('volume')}; "
        f"dominance={btc.get('dominance')}; rv={btc.get('realized_volatility')}.",
        "",
        "## S&P500",
        f"Price={spx_px}; change={spx.get('change_pct')}; "
        f"ATH distance={spx.get('distance_to_ath_pct')}.",
        "",
        "## Macro",
        f"DXY={macro.get('dxy')}; US10Y={macro.get('us10y')}; US02Y={macro.get('us02y')}; "
        f"Gold={macro.get('gold')}; Oil={macro.get('oil')}; Fed={macro.get('fed')}; "
        f"CPI={macro.get('cpi')}; PPI={macro.get('ppi')}; NFP={macro.get('nfp')}.",
        "",
        "## ETF",
        json.dumps(ctx.get("etf") or {}, ensure_ascii=False),
        "",
        "## Derivatives",
        f"Funding={fund}; OI={oi}; Liquidations={ctx.get('liquidations')}.",
        "",
        "## Prediction Markets",
        json.dumps(poly, ensure_ascii=False)[:1500],
        "",
        "## Key Events",
        *ev_lines,
        "",
        "## Risk Factors",
        f"Fear&Greed={fg}; data_availability={ctx.get('data_availability')}.",
        "",
        "## Positive Drivers",
        "- See bullish intel events / ETF inflow narratives when present in context.",
        "",
        "## Negative Drivers",
        "- See bearish intel / macro tightening narratives when present in context.",
        "",
        "## Next 24 Hours",
        "Watch funding extremes, fresh intel events, and macro prints listed in context.",
        "",
        "## Conclusion",
        f"Situational bias {bias} with risk {risk}. Research context only — not advice.",
    ])


class AnthropicLLMClient:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def complete(self, *, system: str, user: str) -> LLMResponse:
        if not self.settings.api_key:
            return TemplateLLMClient(self.settings).complete(system=system, user=user)
        t0 = time.perf_counter()
        body = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.settings.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        last_err: str | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                resp = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=body,
                    timeout=self.settings.timeout_sec,
                )
                if resp.status_code in _RETRYABLE and attempt < self.settings.max_retries:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                parts = []
                for block in data.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text") or ""))
                usage = data.get("usage") or {}
                return LLMResponse(
                    text="".join(parts).strip(),
                    provider="anthropic",
                    model=str(data.get("model") or self.settings.model),
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    usage={
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                    },
                    raw=data,
                )
            except Exception as exc:
                last_err = str(exc)[:500]
                logger.warning("anthropic attempt %s failed: %s", attempt, exc)
                time.sleep(0.4 * (attempt + 1))
        # Fail soft → template
        logger.error("anthropic failed; falling back to template: %s", last_err)
        fb = TemplateLLMClient(self.settings).complete(system=system, user=user)
        fb.error = last_err
        return fb


class OpenAILLMCompatibleClient:
    """OpenAI / OpenRouter / Ollama (OpenAI-compatible /v1/chat/completions)."""

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def complete(self, *, system: str, user: str) -> LLMResponse:
        if self.settings.provider != "ollama" and not self.settings.api_key:
            return TemplateLLMClient(self.settings).complete(system=system, user=user)
        base = (self.settings.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        t0 = time.perf_counter()
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.settings.api_key or 'ollama'}",
        }
        if self.settings.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/polymarket-btc5m-bot"
            headers["X-Title"] = "polymarket-ai-analyst"
        body = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.settings.max_tokens,
            "temperature": 0.2,
        }
        last_err: str | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                resp = requests.post(
                    url, headers=headers, json=body, timeout=self.settings.timeout_sec,
                )
                if resp.status_code in _RETRYABLE and attempt < self.settings.max_retries:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = ""
                choices = data.get("choices") or []
                if choices:
                    text = str((choices[0].get("message") or {}).get("content") or "")
                usage = data.get("usage") or {}
                return LLMResponse(
                    text=text.strip(),
                    provider=self.settings.provider,
                    model=str(data.get("model") or self.settings.model),
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    usage=dict(usage),
                    raw=data,
                )
            except Exception as exc:
                last_err = str(exc)[:500]
                logger.warning("%s attempt %s failed: %s", self.settings.provider, attempt, exc)
                time.sleep(0.4 * (attempt + 1))
        logger.error("%s failed; template fallback: %s", self.settings.provider, last_err)
        fb = TemplateLLMClient(self.settings).complete(system=system, user=user)
        fb.error = last_err
        return fb


def get_llm_client(settings: LLMSettings | None = None) -> LLMClient:
    settings = settings or load_llm_settings()
    provider = settings.provider
    if provider in ("template", "none", "deterministic", ""):
        return TemplateLLMClient(settings)
    if provider == "anthropic":
        return AnthropicLLMClient(settings)
    if provider in ("openai", "openrouter", "ollama"):
        return OpenAILLMCompatibleClient(settings)
    logger.warning("unknown provider %s; using template", provider)
    return TemplateLLMClient(settings)
