"""Provider-neutral AI analyst interface."""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import requests

from bot.research.market_events.ai_analyst.config import (
    AI_API_KEY,
    AI_BASE_URL,
    AI_ENABLED,
    AI_MAX_RETRIES,
    AI_MODEL,
    AI_PROVIDER,
    AI_TIMEOUT_SEC,
    PROMPT_VERSION,
)
from bot.research.market_events.ai_analyst.schema import (
    StructuredAnalysis,
    parse_model_response,
    parse_structured_analysis,
)

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    analysis: StructuredAnalysis | None
    provider: str
    model: str
    prompt_version: str
    latency_ms: float
    token_usage: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    error: str | None = None


class AnalystProvider(ABC):
    @abstractmethod
    def analyze_market_event(self, context_bundle: dict[str, Any]) -> AnalysisResult:
        ...


class NullAnalystProvider(AnalystProvider):
    """Returns INSUFFICIENT_CONTEXT when AI not configured."""

    def analyze_market_event(self, context_bundle: dict[str, Any]) -> AnalysisResult:
        event_id = int(context_bundle.get("event_id", 0))
        symbol = context_bundle.get("market_event", {}).get("symbol", "?")
        analysis = StructuredAnalysis(
            event_id=event_id,
            symbol=symbol,
            analysis_status="INSUFFICIENT_CONTEXT",
            movement_interpretation="UNKNOWN",
            reversal_bias="NO_VIEW",
            confidence=0.0,
            short_commentary="AI analyst disabled or provider not configured.",
        )
        return AnalysisResult(
            analysis=analysis,
            provider="none",
            model="none",
            prompt_version=PROMPT_VERSION,
            latency_ms=0.0,
        )


class DeterministicShadowProvider(AnalystProvider):
    """Rule-based shadow analyst when no LLM API configured."""

    def analyze_market_event(self, context_bundle: dict[str, Any]) -> AnalysisResult:
        t0 = time.perf_counter()
        me = context_bundle.get("market_event", {})
        event_id = int(context_bundle.get("event_id", 0))
        symbol = me.get("symbol", "?")
        classification = me.get("classification", "UNKNOWN")
        rel = me.get("relative_return_pct")
        btc = me.get("btc_return_pct")
        confirmed = context_bundle.get("price_structure", {}).get("confirmed_reversal")

        supporting: list[str] = []
        contradicting: list[str] = []
        interp = "UNKNOWN"
        bias = "WAIT_FOR_CONFIRMATION"

        if classification == "ASSET_SPECIFIC":
            interp = "ASSET_SPECIFIC"
            supporting.append("Classification: asset-specific move")
        elif classification == "MARKET_WIDE":
            interp = "MARKET_WIDE"
            supporting.append("Classification: market-wide move")

        if rel is not None and abs(rel) > 0.5:
            supporting.append(f"Relative vs BTC elevated: {rel:.2f}%")
        if btc is not None and me.get("return_pct") is not None:
            if abs(me["return_pct"]) > abs(btc) * 1.5:
                supporting.append("Move exceeds BTC magnitude")

        if not confirmed:
            contradicting.append("R1-R5 reversal not yet confirmed")
            bias = "WAIT_FOR_CONFIRMATION"
        else:
            bias = "FADE_FAVORED"
            supporting.append(f"Reversal confirmed: {confirmed}")

        ctx_ids = [
            str(c.get("context_id")) for c in context_bundle.get("telegram_context", [])
        ][:8]

        analysis = StructuredAnalysis(
            event_id=event_id,
            symbol=symbol,
            analysis_status="COMPLETE",
            movement_interpretation=interp,
            reversal_bias=bias,
            confidence=0.55 if supporting else 0.35,
            supporting_factors=supporting,
            contradicting_factors=contradicting,
            relevant_context_ids=ctx_ids,
            risk_notes=["Shadow deterministic analyst — not LLM"],
            what_would_change_view=["Reversal confirmation", "BTC confirmation of direction"],
            short_commentary=(
                f"{symbol} {me.get('return_pct', 0):+.2f}% shock; "
                f"interpretation {interp}; bias {bias}."
            ),
        )
        return AnalysisResult(
            analysis=analysis,
            provider="deterministic_shadow",
            model="rules_v1",
            prompt_version=PROMPT_VERSION,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )


class OpenAICompatibleProvider(AnalystProvider):
    """HTTP chat-completions compatible provider."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def analyze_market_event(self, context_bundle: dict[str, Any]) -> AnalysisResult:
        t0 = time.perf_counter()
        event_id = int(context_bundle.get("event_id", 0))
        symbol = context_bundle.get("market_event", {}).get("symbol", "?")
        prompt = _build_prompt(context_bundle)
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        last_err: str | None = None
        raw = ""
        usage: dict[str, Any] = {}
        for attempt in range(AI_MAX_RETRIES + 1):
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=AI_TIMEOUT_SEC)
                resp.raise_for_status()
                data = resp.json()
                raw = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                parsed = parse_model_response(raw, event_id=event_id, symbol=symbol)
                if parsed is None:
                    last_err = "malformed_output"
                    continue
                return AnalysisResult(
                    analysis=parsed,
                    provider=AI_PROVIDER,
                    model=self._model,
                    prompt_version=PROMPT_VERSION,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    token_usage=usage,
                    raw_response=raw[:4000],
                )
            except requests.Timeout:
                last_err = "timeout"
            except Exception as exc:
                last_err = str(exc)
                logger.warning("AI provider attempt %s failed: %s", attempt + 1, exc)
        failed = parse_structured_analysis(
            {
                "analysis_status": "FAILED",
                "movement_interpretation": "UNKNOWN",
                "reversal_bias": "NO_VIEW",
                "confidence": 0.0,
                "short_commentary": last_err or "provider_error",
            },
            event_id=event_id,
            symbol=symbol,
        )
        return AnalysisResult(
            analysis=failed,
            provider=AI_PROVIDER,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            token_usage=usage,
            raw_response=raw[:4000],
            error=last_err,
        )


_SYSTEM_PROMPT = """You are a research analyst for market shock events.
Output ONLY valid JSON matching the required schema.
You do NOT place trades. You provide commentary only.
Never recommend live orders."""


def _build_prompt(bundle: dict[str, Any]) -> str:
    return (
        "Analyze this market shock context bundle and return JSON with fields: "
        "event_id, symbol, analysis_status (COMPLETE|INSUFFICIENT_CONTEXT|FAILED), "
        "movement_interpretation, reversal_bias, confidence (0-1), "
        "supporting_factors[], contradicting_factors[], relevant_context_ids[], "
        "risk_notes[], what_would_change_view[], short_commentary.\n\n"
        f"Context:\n{json.dumps(bundle, default=str)[:12000]}"
    )


def get_analyst_provider() -> AnalystProvider:
    if not AI_ENABLED:
        return NullAnalystProvider()
    if AI_PROVIDER in ("openai", "openai_compatible", "http") and AI_API_KEY and AI_MODEL:
        base = AI_BASE_URL or "https://api.openai.com/v1"
        return OpenAICompatibleProvider(api_key=AI_API_KEY, model=AI_MODEL, base_url=base)
    if AI_PROVIDER == "deterministic":
        return DeterministicShadowProvider()
    return DeterministicShadowProvider()
