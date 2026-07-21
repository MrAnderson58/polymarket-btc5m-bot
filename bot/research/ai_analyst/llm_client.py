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


def _macro_line(macro: dict[str, Any], key: str) -> str:
    block = macro.get(key) or {}
    if isinstance(block, dict):
        val = block.get("value")
        ch = block.get("change_24h")
        trend = block.get("trend")
        if val is not None:
            parts = [f"{key.upper()} {val}"]
            if ch is not None:
                parts.append(f"Δ24h {ch}")
            if trend:
                parts.append(f"({trend})")
            return " ".join(parts)
    return ""


def _bias_label(bias: str) -> str:
    return {
        "bullish": "Moderately Bullish",
        "bearish": "Moderately Bearish",
        "neutral": "Neutral",
    }.get(bias, "Mixed")


def _key_takeaways(*items: str) -> str:
    lines = ["### Key Takeaways"]
    for item in items[:3]:
        lines.append(f"- {item}")
    return "\n".join(lines)


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
    hints = ctx.get("reasoning_hints") or {}
    quality = ctx.get("analysis_quality") or {}
    contradictions = hints.get("contradictions_detected") or []
    narratives = hints.get("narrative_candidates") or []
    pairs = hints.get("cross_asset_pairs_available") or []

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
    spx_px = spx.get("value") or spx.get("price")
    risk = "MEDIUM"
    if (fg.get("current") is not None and float(fg["current"]) <= 25) or abs(sent) > 0.4:
        risk = "HIGH"
    elif abs(sent) < 0.1:
        risk = "LOW"

    conf = quality.get("confidence") or 50
    theme = (
        f"Risk assets show a {bias} tilt; lead narrative: {str(lead)[:120]}."
        if events else "Data coverage limited — interpret with caution."
    )
    drivers = []
    etf = ctx.get("etf") or {}
    btc_etf = etf.get("btc_etf") or {}
    if btc_etf.get("netflow_5d") is not None:
        drivers.append(f"ETF 5d netflow {btc_etf.get('netflow_5d')} USD millions")
    if events:
        drivers.append(str(lead)[:80])
    vix = ctx.get("vix") or {}
    if vix.get("trend"):
        drivers.append(f"VIX trend {vix.get('trend')}")
    if not drivers:
        drivers.append("Insufficient driver data in context")

    risks = []
    if fg.get("current") is not None and float(fg["current"]) < 40:
        risks.append(f"Fear & Greed still cautious ({fg.get('current')})")
    dxy_line = _macro_line(macro, "dxy")
    if dxy_line:
        risks.append(f"Dollar backdrop: {dxy_line}")
    y10_line = _macro_line(macro, "us10y")
    if y10_line:
        risks.append(f"Rates: {y10_line}")
    if not risks:
        risks.append("Event-driven volatility; check data_gaps in context")

    task = _task_header(user)
    if "exact keys" in task or "json object" in task or "market_bias" in task:
        return json.dumps({
            "market_bias": bias,
            "risk_level": risk,
            "main_theme": str(lead)[:180],
            "btc_summary": (
                f"BTC {btc_px}; 24h {btc.get('change_24h_pct')}% — "
                f"{'flat despite supportive flows' if bias == 'bullish' else 'tracking macro tone'}"
            ),
            "sp500_summary": f"SPX {spx_px}; trend {spx.get('trend') or 'n/a'}",
            "macro_summary": "; ".join(filter(None, [
                _macro_line(macro, "dxy"), _macro_line(macro, "us10y"),
            ])) or "Macro fields sparse",
            "top_events": [str(e.get("title") or "")[:120] for e in events[:5]],
            "top_risks": risks[:5],
            "next_24h": "Monitor ETF flows, intel freshness, funding, and macro prints.",
            "analysis_quality": quality,
        }, ensure_ascii=False, indent=2)

    if "280 characters" in task or "x/twitter" in task:
        obs = str(lead)[:70] if events else "Mixed signals"
        risk_bit = risks[0][:50] if risks else f"Risk {risk}"
        parts = [
            f"{theme[:90]}",
            f"{obs}.",
            f"Risk: {risk_bit}.",
            "Context only.",
        ]
        return " ".join(parts)[:280]

    if "telegram" in task or "русский" in task:
        lines = [
            "📰 Market Snapshot",
            f"Рынок {bias}, риск {risk}. {str(lead)[:140]}",
            "",
            "📈 Main Driver",
            drivers[0][:160],
            "",
            "⚠ Main Risk",
            risks[0][:160],
            "",
            "₿ Bitcoin",
            f"BTC {btc_px}; 24h {btc.get('change_24h_pct')}% — интерпретация по контексту.",
            "",
            "📊 Equities",
            f"SPX {spx_px}; VIX {vix.get('value')}.",
            "",
            "🏛 Macro",
            (_macro_line(macro, "dxy") or "Macro data limited")[:120],
            "",
            "👀 What to Watch",
            "ETF потоки, свежие intel-события, funding.",
            "",
            "(No trading advice)",
        ]
        return "\n".join(lines)[:1200]

    if "morning brief" in task:
        return "\n".join([
            "## Morning Brief",
            f"**Bias:** {_bias_label(bias)} | **Risk:** {risk} | **Confidence:** {conf}%",
            f"**Theme:** {theme}",
            f"**Macro:** {_macro_line(macro, 'dxy') or 'n/a'}; {_macro_line(macro, 'us10y') or 'n/a'}",
            f"**BTC:** {btc_px} (24h {btc.get('change_24h_pct')}%)",
            f"**Watch:** {drivers[0]}",
            "**Note:** Template mode — LLM not configured. No trading advice.",
        ])

    if "evening brief" in task:
        return "\n".join([
            "## Evening Brief",
            f"**Day bias:** {_bias_label(bias)} | **Risk:** {risk}",
            f"**What moved:** {lead}",
            f"**BTC 24h:** {btc.get('change_24h_pct')}% | **SPX:** {spx.get('change_pct') or spx.get('change_24h')}",
            "**Overnight watch:** funding, OI delta, fresh intel events.",
            "**Note:** Template mode. No trading advice.",
        ])

    if "btc brief" in task or "bitcoin-only" in task:
        return "\n".join([
            "## BTC Brief",
            "",
            "### Market State",
            f"BTC trades near {btc_px}; 24h change {btc.get('change_24h_pct')}%. "
            f"Lead intel: {lead}. Template reasoning — check ETF and macro for offsetting forces.",
            "",
            "### Bullish Factors",
            "- " + (drivers[0] if drivers else "Limited bullish evidence in context"),
            "- Positive intel sentiment when present in top_events",
            "",
            "### Bearish Factors",
            "- " + (risks[0] if risks else "Macro/funding risks per context"),
            f"- Funding {fund.get('current')}; elevated funding can cap upside",
            "",
            "### Critical Levels",
            "Not available in context.",
            "",
            "### What to Watch Next",
            "- ETF netflows and intel event freshness",
            "- Funding and open interest shifts",
            "- Dollar and yield moves vs BTC",
            "",
            _key_takeaways(
                "BTC tone follows macro + flow mix in context",
                "Watch contradictions between flows and price",
                "No trade advice — monitoring only",
            ),
            "",
            "No trading advice.",
        ])

    if "macro brief" in task:
        return "\n".join([
            "## Macro Brief",
            "",
            "### Macro Regime",
            f"{'Risk-on with macro cross-currents' if bias == 'bullish' else 'Cautious macro backdrop'}. "
            "Dollar and yields set the tone for risk assets.",
            "",
            "### Dollar",
            _macro_line(macro, "dxy") or "DXY data unavailable — limits USD read.",
            "A firmer dollar typically pressures BTC and multinationals.",
            "",
            "### Rates",
            _macro_line(macro, "us10y") or "US10Y unavailable.",
            _macro_line(macro, "us02y") or "",
            "",
            "### Commodities",
            _macro_line(macro, "gold") or "Gold: n/a",
            _macro_line(macro, "oil") or "Oil: n/a",
            "",
            "### Macro Risk",
            f"Fed narrative: {macro.get('fed') or 'see context'}; risk level {risk}.",
            "",
            "### Bottom Line",
            f"Macro transmission to BTC via risk tone; SPX at {spx_px}.",
            "",
            _key_takeaways(
                "Macro sets the ceiling/floor for risk appetite",
                "Rates and USD are primary BTC headwind/tailwind",
                "Gaps reduce conviction — see data_gaps",
            ),
            "",
            "No trading advice.",
        ])

    if "s&p500 brief" in task or "sp500 brief" in task:
        return "\n".join([
            "## S&P500 Brief",
            "",
            "### Risk Appetite",
            f"SPX {spx_px}; trend {spx.get('trend') or 'n/a'}. "
            f"F&G {fg.get('current')}. Equities reflect {bias} bias.",
            "",
            "### Breadth",
            "Sector breadth unavailable in context.",
            "",
            "### Volatility",
            f"VIX {vix.get('value')} ({vix.get('trend') or 'n/a'}). "
            "Lower VIX supports equity risk-taking when confirmed by price.",
            "",
            "### BTC Correlation",
            f"BTC {btc_px} — shared risk tone; both sensitive to macro and flows.",
            "",
            "### Bottom Line",
            f"Equities {_bias_label(bias).lower()} with {risk} macro risk.",
            "",
            _key_takeaways(
                "VIX and sentiment gauge equity comfort",
                "BTC moves often align with equity risk days",
                "Breadth data would sharpen the read",
            ),
            "",
            "No trading advice.",
        ])

    # Full report default (S46.2 structure)
    ev_lines = []
    for e in events[:5]:
        ev_lines.append(
            f"- **{e.get('title')}** — {str(e.get('why_it_matters') or e.get('summary') or '')[:160]}"
        )
    if not ev_lines:
        ev_lines = ["- Limited intel event coverage"]

    cross_lines = []
    for pair in pairs:
        cross_lines.append(f"**{pair}:** Relationship active in context — interpret co-movement, not forecast.")
    if not cross_lines:
        cross_lines = ["- Insufficient paired data for cross-asset analysis"]

    contra_lines = []
    for c in contradictions:
        contra_lines.append(f"- {c}")
    if not contra_lines:
        contra_lines = ["- No major contradictions detected in available data"]

    narr_lines = []
    for n in narratives[:5]:
        narr_lines.append(
            f"- **{n.get('narrative')}** ({n.get('strength')}): {n.get('evidence')}"
        )

    exec_summary = "\n".join([
        "## Executive Summary",
        "",
        f"Today's Theme:\n{theme}",
        "",
        f"Market Bias:\n{_bias_label(bias)}",
        "",
        f"Confidence:\n{conf}%",
        "",
        "Key Drivers:",
        *[f"• {d}" for d in drivers[:4]],
        "",
        "Main Risks:",
        *[f"• {r}" for r in risks[:4]],
        "",
        "Cross-asset setup reflects mixed macro and flow signals. Template analyst — context only.",
    ])

    sections = [
        exec_summary,
        "",
        "## Global Market Overview",
        f"Risk environment: {risk}. BTC {btc_px}, SPX {spx_px}, VIX {vix.get('value')}, "
        f"F&G {fg.get('current')}. Dollar and yields shape the backdrop.",
        _key_takeaways(
            "Macro and flows jointly set risk tone",
            "BTC and equities share macro sensitivity",
            "Check contradictions below for tension points",
        ),
        "",
        "## Cross-Asset Relationships",
        *cross_lines,
        _key_takeaways(
            "Compare direction, not isolated prints",
            "Dollar/yields often lead crypto lag",
            "Gold can diverge when real rates shift",
        ),
        "",
        "## Market Contradictions",
        *contra_lines,
        _key_takeaways(
            "Contradictions flag unstable narratives",
            "Flows vs price is a common BTC tension",
            "Resolve with fresh data, not assumptions",
        ),
        "",
        "## Current Narrative",
        *narr_lines,
        _key_takeaways(
            "Narratives need evidence from context",
            "Strength reflects data density",
            "Weak themes imply limited coverage",
        ),
        "",
        "## Bitcoin",
        f"BTC near {btc_px}; 1h {btc.get('change_1h_pct')}%, 24h {btc.get('change_24h_pct')}%. "
        f"Dominance {btc.get('dominance')}. Drivers: {drivers[0] if drivers else 'n/a'}.",
        _key_takeaways(
            "Price action vs flows defines near-term tone",
            "Derivatives positioning matters for squeeze risk",
            "Macro headwinds can offset ETF demand",
        ),
        "",
        "## S&P500",
        f"SPX {spx_px}; change {spx.get('change_pct') or spx.get('change_24h')}; "
        f"ATH distance {spx.get('distance_to_ath_pct')}.",
        _key_takeaways(
            "Equity trend supports or drags BTC beta",
            "VIX compression aids risk-on",
            "Breadth unknown limits conviction",
        ),
        "",
        "## Macro",
        "; ".join(filter(None, [
            _macro_line(macro, "dxy"), _macro_line(macro, "us10y"),
            _macro_line(macro, "us02y"), _macro_line(macro, "gold"),
            _macro_line(macro, "oil"),
        ])) or "Macro block sparse.",
        _key_takeaways(
            "Dollar and yields are primary transmission",
            "Commodities inform inflation growth mix",
            "Fed/CPI fields anchor regime call",
        ),
        "",
        "## ETF",
        json.dumps(etf, ensure_ascii=False)[:800],
        "Numeric netflows interpret institutional demand — link to BTC, not headline noise.",
        _key_takeaways(
            "Sustained inflows support medium-term bid",
            "Flat price + inflows = absorption or lag",
            "ETH ETF adds alt risk context",
        ),
        "",
        "## Derivatives",
        f"Funding {fund}; OI {oi}; Liquidations {ctx.get('liquidations')}.",
        "Elevated funding suggests crowded longs; watch for mean reversion.",
        _key_takeaways(
            "Funding extremes flag positioning stress",
            "OI changes confirm trend participation",
            "Liquidations can accelerate moves",
        ),
        "",
        "## Prediction Markets",
        json.dumps(poly, ensure_ascii=False)[:1200],
        _key_takeaways(
            "Polymarket reflects event-priced risk",
            "Compare to spot narrative for gaps",
            "Low liquidity limits signal weight",
        ),
        "",
        "## Key Events",
        *ev_lines,
        _key_takeaways(
            "Intel events drive narrative shifts",
            "Use why_it_matters for transmission",
            "Freshness affects relevance",
        ),
        "",
        "## Risk Factors",
        f"Fear&Greed {fg.get('current')}; completeness {quality.get('context_completeness')}%.",
        _key_takeaways(*risks[:3]),
        "",
        "## Positive Drivers",
        *[f"- {d}" for d in drivers[:3]],
        _key_takeaways("See bullish intel and ETF when present", "Macro tailwinds if DXY soft", "Risk-on equity tone helps BTC"),
        "",
        "## Negative Drivers",
        *[f"- {r}" for r in risks[:3]],
        _key_takeaways("Macro tightening weighs on beta", "Low sentiment caps rallies", "Event risk from intel calendar"),
        "",
        "## Next 24 Hours",
        "Watch funding extremes, fresh intel events, macro prints, and ETF flow updates.",
        _key_takeaways(
            "Monitor contradictions for resolution",
            "Overnight macro can reprice crypto",
            "No trade actions — observation only",
        ),
        "",
        "## Conclusion",
        f"Situational bias {_bias_label(bias)} with {risk} risk and {conf}% confidence. "
        "Research context only — not investment advice.",
    ]
    return "\n".join(sections)


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
